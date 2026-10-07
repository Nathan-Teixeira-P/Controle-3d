"""Regras de negócio: estoque (livro-razão), custo de produto, pedidos, vendas, financeiro, dashboard."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import Session, func, select

from .calc import calcular
from .models import (Canal, Config, Impressora, Insumo, Item, Lancamento, Material, MovEstoque, Orcamento, Pedido,
                     PedidoHistorico, PedidoItem, Produto, ProdutoMaterial, Rolo, Tarefa, Venda, VendaItem, agora)

ORDEM = ["aguardando", "imprimindo", "acabamento", "pronto", "enviado", "entregue"]
Z = Decimal(0)


def ordem(p):
    return ORDEM[:4] if p.tipo == "estoque" else ORDEM


def add_months(d, n=1):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def get_config(s: Session) -> Config:
    c = s.get(Config, 1)
    if not c:
        c = Config()
        s.add(c); s.commit(); s.refresh(c)
    return c


# ---------- Estoque
def mov(s, tipo, item_id, delta, motivo, ref_tipo="", ref_id=0, obs=""):
    s.add(MovEstoque(item_tipo=tipo, item_id=item_id, delta=Decimal(delta), motivo=motivo,
                     ref_tipo=ref_tipo, ref_id=ref_id, obs=obs))


def saldos(s, tipo):
    rows = s.exec(select(MovEstoque.item_id, func.sum(MovEstoque.delta)).where(MovEstoque.item_tipo == tipo)
                  .group_by(MovEstoque.item_id)).all()
    return {i: Decimal(str(v)) for i, v in rows}


def saldo(s, tipo, item_id):
    return saldos(s, tipo).get(item_id, Z)


def custo_g_material(s, material_id, sd=None):
    """Média ponderada pelo saldo dos rolos em estoque; sem saldo, custo/g do último rolo comprado."""
    sd = sd if sd is not None else saldos(s, "rolo")
    rolos = s.exec(select(Rolo).where(Rolo.material_id == material_id).order_by(Rolo.id)).all()
    peso = sum((sd.get(r.id, Z) for r in rolos if sd.get(r.id, Z) > 0), Z)
    if peso > 0:
        return sum((sd[r.id] * r.custo_g for r in rolos if sd.get(r.id, Z) > 0), Z) / peso
    return rolos[-1].custo_g if rolos else Z


def estoque_baixo(s):
    """Alertas de estoque: 'acabou' (saldo <= 0) e 'acabando' (saldo <= mínimo). Só itens acompanhados:
    com mínimo definido ou que já tiveram entrada. Retorna dicts, 'acabou' primeiro."""
    out = []

    def add(tipo, nome, key, sl, minimo, acompanhado):
        if acompanhado and sl <= 0:
            out.append(dict(tipo=tipo, nome=nome, key=key, saldo=sl, minimo=minimo, status="acabou"))
        elif acompanhado and minimo > 0 and sl <= minimo:
            out.append(dict(tipo=tipo, nome=nome, key=key, saldo=sl, minimo=minimo, status="acabando"))

    def com_entrada(tipo):
        return set(s.exec(select(MovEstoque.item_id).where(MovEstoque.item_tipo == tipo, MovEstoque.delta > 0).distinct()).all())

    sd = saldos(s, "rolo")
    for m in s.exec(select(Material).where(Material.ativo)).all():
        rolos = s.exec(select(Rolo).where(Rolo.material_id == m.id, Rolo.ativo)).all()
        add("Filamento", m.nome, ("material", m.id), sum((sd.get(r.id, Z) for r in rolos), Z), m.minimo_g, bool(rolos) or m.minimo_g > 0)
    si, ei = saldos(s, "insumo"), com_entrada("insumo")
    for i in s.exec(select(Insumo).where(Insumo.ativo)).all():
        add("Insumo", i.nome, ("insumo", i.id), si.get(i.id, Z), i.minimo, i.minimo > 0 or i.id in ei)
    sp, ep = saldos(s, "produto"), com_entrada("produto")
    for p in s.exec(select(Produto).where(Produto.ativo)).all():
        add("Produto pronto", p.nome, ("produto", p.id), sp.get(p.id, Z), p.minimo, p.minimo > 0 or p.id in ep)
    return sorted(out, key=lambda a: a["status"] != "acabou")


# ---------- Custo de produto
def custo_produto(s, p: Produto, cfg=None, canal: Canal | None = None, sd=None):
    cfg = cfg or get_config(s)
    sd = sd if sd is not None else saldos(s, "rolo")
    fil = sum((pm.gramas * custo_g_material(s, pm.material_id, sd) for pm in p.materiais), Z)
    ins = sum((pi.qtd * pi.insumo.custo_unit for pi in p.insumos), Z)
    imp = s.get(Impressora, p.impressora_id) if p.impressora_id else None
    return calcular(cfg, imp, fil, Decimal(p.tempo_min) / 60, p.embalagem, p.acessorios + ins, p.min_trabalho,
                    p.margem_pct, canal.taxa_pct if canal else 0, canal.taxa_fixa if canal else 0)


def margem_real(p, custo):
    return (p.preco - custo) / p.preco * 100 if p.preco else Z


# ---------- Orçamento → Pedido
def criar_pedido_de_orcamento(s, o: Orcamento, prazo: date | None):
    cfg = get_config(s)
    ped = Pedido(orcamento_id=o.id, cliente_id=o.cliente_id, canal_id=o.canal_id, prazo=prazo,
                 desconto=o.desconto, frete=o.frete, observacoes=o.observacoes)
    s.add(ped); s.flush()
    for i in o.itens:
        custo = i.custo_unit
        if i.produto_id:
            custo = custo_produto(s, s.get(Produto, i.produto_id), cfg)["custo"]
        s.add(PedidoItem(pedido_id=ped.id, produto_id=i.produto_id, descricao=i.descricao,
                         personalizacao=i.personalizacao, qtd=i.qtd, preco_unit=i.preco_unit, custo_unit=custo))
    s.add(PedidoHistorico(pedido_id=ped.id, de="", para="aguardando"))
    o.status = "aprovado"
    s.add(o); s.commit(); s.refresh(ped)
    return ped


def plano_consumo(s, ped):
    """Materiais necessários para o pedido, com os rolos que ainda têm saldo."""
    need = {}
    for it in ped.itens:
        if it.produto_id:
            for pm in s.exec(select(ProdutoMaterial).where(ProdutoMaterial.produto_id == it.produto_id)):
                need[pm.material_id] = need.get(pm.material_id, Z) + pm.gramas * it.qtd
    sd, out = saldos(s, "rolo"), []
    for mid, g in need.items():
        rolos = [(r, sd.get(r.id, Z)) for r in s.exec(select(Rolo).where(Rolo.material_id == mid, Rolo.ativo).order_by(Rolo.id))
                 if sd.get(r.id, Z) > 0]
        out.append(dict(material=s.get(Material, mid), gramas=g, rolos=rolos))
    return out


# ---------- Pedido: máquina de estados
def mover_pedido(s, ped, para, consumos=()):
    """consumos: [(rolo_id, gramas)]. Retorna mensagem de erro ou None."""
    de, o = ped.status, ordem(ped)
    if para == "cancelado":
        if de in ("entregue", "cancelado") or (ped.tipo == "estoque" and de == "pronto"):
            return "Este pedido não pode mais ser cancelado."
    elif de == "cancelado":
        if para != "aguardando":
            return "Pedido cancelado só pode ser reaberto."
    elif de == "entregue" or de not in o or para not in o or abs(o.index(para) - o.index(de)) != 1:
        return "Movimento inválido."

    if de == "aguardando" and para == "imprimindo" and not ped.do_estoque:
        for rolo_id, g in consumos:
            if g > 0:
                mov(s, "rolo", rolo_id, -g, "producao", "pedido", ped.id)
    if de == "acabamento" and para == "pronto":
        for it in ped.itens:
            if not it.produto_id:
                continue
            p = s.get(Produto, it.produto_id)
            if ped.tipo == "estoque":
                mov(s, "produto", p.id, it.qtd, "producao", "pedido", ped.id)
            if not ped.do_estoque and p.impressora_id:
                imp = s.get(Impressora, p.impressora_id)
                imp.horas_acumuladas += Decimal(p.tempo_min * it.qtd) / 60
                s.add(imp)
    if de == "pronto" and para == "acabamento" and ped.tipo == "estoque":
        for m in s.exec(select(MovEstoque).where(MovEstoque.ref_tipo == "pedido", MovEstoque.ref_id == ped.id,
                                                 MovEstoque.item_tipo == "produto", MovEstoque.motivo == "producao")):
            mov(s, "produto", m.item_id, -m.delta, "ajuste", "pedido", ped.id, "estorno: voltou para acabamento")
    if para == "entregue":
        criar_venda_de_pedido(s, ped)
    s.add(PedidoHistorico(pedido_id=ped.id, de=de, para=para))
    ped.status = para
    s.add(ped); s.commit()


def registrar_falha(s, ped, rolo_id, gramas, obs=""):
    r = s.get(Rolo, rolo_id)
    mov(s, "rolo", rolo_id, -gramas, "falha", "pedido", ped.id, obs)
    ped.custo_extra += gramas * r.custo_g
    s.add(ped); s.commit()


# ---------- Vendas
def _taxa(canal, total):
    return (total * canal.taxa_pct / 100 + canal.taxa_fixa) if canal else Z


def _lancar_receita(s, v: Venda):
    s.add(Lancamento(tipo="receita", categoria="Vendas", natureza="variavel", descricao=f"Venda #{v.id}",
                     valor=v.total - v.taxa, vencimento=v.data, pago_em=v.data, venda_id=v.id))


def criar_venda_de_pedido(s, ped):
    if s.exec(select(Venda).where(Venda.pedido_id == ped.id, Venda.status == "ativa")).first():
        return
    canal = s.get(Canal, ped.canal_id) if ped.canal_id else None
    total = ped.total
    custo = sum((i.qtd * i.custo_unit for i in ped.itens), Z) + ped.custo_extra
    taxa = _taxa(canal, total)
    v = Venda(pedido_id=ped.id, cliente_id=ped.cliente_id, canal_id=ped.canal_id, total=total, custo=custo,
              taxa=taxa, lucro=total - taxa - custo)
    s.add(v); s.flush()
    for i in ped.itens:
        s.add(VendaItem(venda_id=v.id, produto_id=i.produto_id, descricao=i.descricao, qtd=i.qtd,
                        preco_unit=i.preco_unit, custo_unit=i.custo_unit))
        if ped.do_estoque and i.produto_id:
            mov(s, "produto", i.produto_id, -i.qtd, "venda", "venda", v.id)
    _lancar_receita(s, v)
    return v


def venda_avulsa(s, cliente_id, canal_id, forma, data, linhas):
    """linhas: [(produto_id|None, descricao, qtd, preco_unit)]. Baixa o estoque pronto dos produtos."""
    cfg, canal = get_config(s), (s.get(Canal, canal_id) if canal_id else None)
    v = Venda(cliente_id=cliente_id, canal_id=canal_id, forma_pagamento=forma, data=data)
    s.add(v); s.flush()
    total = custo = Z
    for pid, desc, qtd, preco in linhas:
        c = Z
        if pid:
            p = s.get(Produto, pid)
            c, desc = custo_produto(s, p, cfg)["custo"], desc or p.nome
            mov(s, "produto", pid, -qtd, "venda", "venda", v.id)
        s.add(VendaItem(venda_id=v.id, produto_id=pid, descricao=desc, qtd=qtd, preco_unit=preco, custo_unit=c))
        total += qtd * preco; custo += qtd * c
    v.total, v.custo, v.taxa = total, custo, _taxa(canal, total)
    v.lucro = total - v.taxa - custo
    s.add(v); s.flush()
    _lancar_receita(s, v)
    s.commit()
    return v


def estornar_venda(s, v: Venda):
    if v.status == "estornada":
        return
    v.status = "estornada"
    for l in s.exec(select(Lancamento).where(Lancamento.venda_id == v.id)):
        l.cancelado = True; s.add(l)
    for m in s.exec(select(MovEstoque).where(MovEstoque.ref_tipo == "venda", MovEstoque.ref_id == v.id)):
        mov(s, m.item_tipo, m.item_id, -m.delta, "ajuste", "venda", v.id, "estorno de venda")
    s.add(v); s.commit()


# ---------- Financeiro
def pagar(s, l: Lancamento, quando: date):
    l.pago_em = quando
    if l.recorrencia == "mensal" and not l.gerou_proximo:
        s.add(Lancamento(tipo=l.tipo, categoria=l.categoria, natureza=l.natureza, descricao=l.descricao,
                         valor=l.valor, vencimento=add_months(l.vencimento), recorrencia="mensal"))
        l.gerou_proximo = True
    s.add(l); s.commit()


def concluir_tarefa(s, t: Tarefa):
    t.feita = not t.feita
    if t.feita and t.recorrencia:
        s.add(Tarefa(titulo=t.titulo, hora=t.hora, recorrencia=t.recorrencia,
                     data=t.data + timedelta(days=1 if t.recorrencia == "diaria" else 7)))
    s.add(t); s.commit()


def totais_mes(s, ini: date, fim: date):
    """Dados do período [ini, fim): vendas ativas e despesas pagas."""
    vs = s.exec(select(Venda).where(Venda.status == "ativa", Venda.data >= ini, Venda.data < fim)).all()
    desp = s.exec(select(func.coalesce(func.sum(Lancamento.valor), 0)).where(
        Lancamento.tipo == "despesa", Lancamento.cancelado == False, Lancamento.pago_em >= ini, Lancamento.pago_em < fim)).one()  # noqa: E712
    fat = sum((v.total for v in vs), Z)
    cmv = sum((v.custo for v in vs), Z)
    taxas = sum((v.taxa for v in vs), Z)
    desp = Decimal(str(desp))
    return dict(vendas=vs, n=len(vs), faturamento=fat, cmv=cmv, taxas=taxas, despesas=desp,
                custos=cmv + desp, lucro=fat - taxas - cmv - desp)


def dashboard(s, hoje: date):
    ini = hoje.replace(day=1)
    t = totais_mes(s, ini, add_months(ini))
    top = {}
    for v in t["vendas"]:
        for i in v.itens:
            top[i.descricao] = top.get(i.descricao, 0) + i.qtd
    q = select(Tarefa).order_by(Tarefa.data, Tarefa.hora)
    abertos = s.exec(select(Orcamento).where(Orcamento.status == "aberto")).all()
    ativos = s.exec(select(Pedido).where(Pedido.status.in_(["aguardando", "imprimindo", "acabamento", "pronto", "enviado"]))).all()
    prazos = [p for p in ativos if p.prazo and p.prazo <= hoje + timedelta(days=7) and not (p.tipo == "estoque" and p.status == "pronto")]
    return dict(
        **t, em_producao=[p for p in ativos if p.status in ("imprimindo", "acabamento")],
        atrasados=[p for p in ativos if p.atrasado], baixo=estoque_baixo(s),
        mais_vendidos=sorted(top.items(), key=lambda x: -x[1])[:5],
        atrasadas=s.exec(q.where(Tarefa.data < hoje, Tarefa.feita == False)).all(),  # noqa: E712
        de_hoje=s.exec(q.where(Tarefa.data == hoje)).all(),
        agenda=s.exec(q.where(Tarefa.data > hoje, Tarefa.data <= hoje + timedelta(days=7))).all(),
        prazos=sorted(prazos, key=lambda p: p.prazo),
        n_abertos=len(abertos), v_abertos=sum((o.total for o in abertos), Z))
