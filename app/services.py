"""Regras de negócio: estoque (livro-razão), custo de produto, pedidos, vendas, financeiro, dashboard."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import Session, func, select

from .calc import calcular
from .util import D
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


def baixar_rolo(s, rolo_id, gramas, ref_tipo, ref_id, motivo="producao", obs=""):
    """Baixa `gramas` do rolo escolhido; o que faltar sai dos outros rolos do mesmo material (mais antigo primeiro).
    Se ainda faltar, o saldo do rolo escolhido fica negativo (vira alerta 'acabou'). Retorna o que faltou."""
    falta, r = Decimal(gramas), s.get(Rolo, rolo_id)
    sd = saldos(s, "rolo")
    outros = s.exec(select(Rolo).where(Rolo.material_id == r.material_id, Rolo.ativo, Rolo.id != r.id).order_by(Rolo.id)).all()
    for x in [r, *outros]:
        take = min(falta, max(sd.get(x.id, Z), Z))
        if take > 0:
            mov(s, "rolo", x.id, -take, motivo, ref_tipo, ref_id, obs); falta -= take
    if falta > 0:
        mov(s, "rolo", r.id, -falta, motivo, ref_tipo, ref_id, obs or "estoque insuficiente")
    return falta


def registrar_producao(s, p: Produto, unidades, ref_tipo="produto", ref_id=0, obs="produção registrada"):
    """Produção direta (sem pedido): entra no estoque pronto e abate filamento (FIFO entre rolos) e insumos da receita.
    Os valores do produto são do lote, então cada unidade consome 1/lote. Retorna frases do que foi abatido."""
    unidades, lote, sd, resumo = Decimal(unidades), p.lote_qtd or 1, saldos(s, "rolo"), []
    for pm in p.materiais:
        g = pm.gramas * unidades / lote
        rolos = s.exec(select(Rolo).where(Rolo.material_id == pm.material_id, Rolo.ativo).order_by(Rolo.id)).all()
        if not rolos:
            resumo.append(f"{pm.material.nome}: nenhum rolo cadastrado, nada abatido")
            continue
        falta = baixar_rolo(s, next((r for r in rolos if sd.get(r.id, Z) > 0), rolos[0]).id, g, ref_tipo, ref_id, obs=obs)
        resumo.append(f"{g:.0f} g de {pm.material.nome}" + (f" (faltaram {falta:.0f} g no estoque)" if falta else ""))
    for pi in p.insumos:
        q = pi.qtd * unidades / lote
        mov(s, "insumo", pi.insumo_id, -q, "producao", ref_tipo, ref_id, obs)
        resumo.append(f"{q:.2f} {pi.insumo.unidade} de {pi.insumo.nome}")
    mov(s, "produto", p.id, unidades, "producao", ref_tipo, ref_id, obs)
    return resumo


# ---------- Alertas de falta (barreira antes de produzir/vender sem estoque)
def disponivel_filamento(s, material_id):
    sd = saldos(s, "rolo")
    return sum((max(sd.get(r.id, Z), Z) for r in s.exec(select(Rolo).where(Rolo.material_id == material_id, Rolo.ativo))), Z)


def faltas_filamento(s, por_material):
    """por_material: {material_id: gramas}."""
    out = []
    for mid, g in por_material.items():
        disp = disponivel_filamento(s, mid)
        if g > disp:
            out.append(f"Filamento {s.get(Material, mid).nome}: serão usados {g:.0f} g, há {disp:.0f} g em estoque (faltam {g - disp:.0f} g)")
    return out


def faltas_insumos(s, por_insumo):
    sd, out = saldos(s, "insumo"), []
    for iid, q in por_insumo.items():
        disp = max(sd.get(iid, Z), Z)
        if q > disp:
            i = s.get(Insumo, iid)
            out.append(f"Insumo {i.nome}: serão usados {q:.2f} {i.unidade}, há {disp:.2f} em estoque (faltam {q - disp:.2f})")
    return out


def faltas_producao(s, p: Produto, unidades):
    """Falta de filamento/insumo para produzir `unidades` do produto (valores do produto são do lote)."""
    lote, mats, ins = p.lote_qtd or 1, {}, {}
    for pm in p.materiais:
        mats[pm.material_id] = mats.get(pm.material_id, Z) + pm.gramas * Decimal(unidades) / lote
    for pi in p.insumos:
        ins[pi.insumo_id] = ins.get(pi.insumo_id, Z) + pi.qtd * Decimal(unidades) / lote
    return faltas_filamento(s, mats) + faltas_insumos(s, ins)


def faltas_pronto(s, linhas):
    """linhas: [(produto_id, qtd)] que vão sair do estoque pronto."""
    tot, out = {}, []
    for pid, q in linhas:
        if pid:
            tot[pid] = tot.get(pid, Z) + Decimal(q)
    for pid, q in tot.items():
        disp = max(saldo(s, "produto", pid), Z)
        if q > disp:
            out.append(f"Produto {s.get(Produto, pid).nome}: saem {q:.0f} un., há {disp:.0f} prontas em estoque (faltam {q - disp:.0f})")
    return out


def faltas_consumo(s, ped, consumos, insumos):
    """Pedido iniciando a impressão: o que foi digitado (ou o previsto, se não escolheu rolo) contra o estoque."""
    digitado = {}
    for rolo_id, g in consumos:
        mid = s.get(Rolo, rolo_id).material_id
        digitado[mid] = digitado.get(mid, Z) + g
    mats = {pl["material"].id: digitado.get(pl["material"].id, pl["gramas"]) for pl in plano_consumo(s, ped)}
    ins = {pi["insumo"].id: Z for pi in plano_insumos(s, ped)}
    for iid, q in insumos:
        ins[iid] = ins.get(iid, Z) + q
    return faltas_filamento(s, mats) + faltas_insumos(s, ins)


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
def _col(f, k, n):
    v = f.getlist(k)
    return v + [""] * (n - len(v))


def calcular_form(s, f, cfg=None):
    """Calcula a partir de um formulário (calculadora ou ficha do produto). Todos os valores são do LOTE.
    Campos: mat_id/mat_g[/mat_preco/mat_pesorolo] (filamentos; sem mat_id = valor manual), ins_id/ins_q (insumos do estoque),
    out_desc/out_valor (outros custos), horas ou tempo_min, min_trabalho, embalagem, acessorios, margem_pct, lote_qtd,
    impressora_id, canal_id."""
    cfg = cfg or get_config(s)
    imp = s.get(Impressora, int(f["impressora_id"])) if f.get("impressora_id") else None
    canal = s.get(Canal, int(f["canal_id"])) if f.get("canal_id") else None
    n = len(f.getlist("mat_g"))
    fils, detalhe, fil_total = [], [], Z
    for m, g, pr, pesor in zip(_col(f, "mat_id", n), f.getlist("mat_g"), _col(f, "mat_preco", n), _col(f, "mat_pesorolo", n)):
        if D(g) <= 0:
            continue
        fils.append(dict(material_id=m, g=g, preco=pr, peso_rolo=pesor or "1000"))
        if m:
            mat = s.get(Material, int(m))
            cg, nome = custo_g_material(s, mat.id), mat.nome
        else:
            cg, nome = D(pr) / (D(pesor, 1000) or 1000), "Manual"
        detalhe.append(dict(nome=nome, g=D(g), cg=cg, custo=D(g) * cg))
        fil_total += D(g) * cg
    n = len(f.getlist("ins_q"))
    ins, ins_det, ins_total = [], [], Z
    for i, q in zip(_col(f, "ins_id", n), f.getlist("ins_q")):
        if i and D(q) > 0:
            o = s.get(Insumo, int(i))
            ins.append(dict(insumo_id=i, q=q))
            ins_det.append(dict(nome=o.nome, q=D(q), un=o.unidade, custo=D(q) * o.custo_unit))
            ins_total += D(q) * o.custo_unit
    n = len(f.getlist("out_valor"))
    outros = [dict(desc=d, valor=v) for d, v in zip(_col(f, "out_desc", n), f.getlist("out_valor")) if D(v) != 0]
    out_total = sum((D(o["valor"]) for o in outros), Z)
    horas = D(f.get("horas")) if f.get("horas") not in (None, "") else D(f.get("tempo_min")) / 60
    kw = dict(embalagem=D(f.get("embalagem")), acessorios=D(f.get("acessorios")) + ins_total + out_total,
              min_trabalho=D(f.get("min_trabalho")), margem_pct=D(f.get("margem_pct"), cfg.margem_pct),
              lote=int(D(f.get("lote_qtd"), 1)))
    r = calcular(cfg, imp, fil_total, horas, taxa_pct=canal.taxa_pct if canal else 0,
                 taxa_fixa=canal.taxa_fixa if canal else 0, **kw)
    return dict(r=r, fils=fils, detalhe=detalhe, ins=ins, ins_det=ins_det, outros=outros, out_total=out_total,
                cfg=cfg, imp=imp, fil_total=fil_total, horas=horas, kw=kw)


def custo_produto(s, p: Produto, cfg=None, canal: Canal | None = None, sd=None):
    cfg = cfg or get_config(s)
    sd = sd if sd is not None else saldos(s, "rolo")
    fil = sum((pm.gramas * custo_g_material(s, pm.material_id, sd) for pm in p.materiais), Z)
    ins = sum((pi.qtd * pi.insumo.custo_unit for pi in p.insumos), Z)
    imp = s.get(Impressora, p.impressora_id) if p.impressora_id else None
    return calcular(cfg, imp, fil, Decimal(p.tempo_min) / 60, p.embalagem, p.acessorios + ins, p.min_trabalho,
                    p.margem_pct, canal.taxa_pct if canal else 0, canal.taxa_fixa if canal else 0, lote=p.lote_qtd)


def atualizar_precos(s, produtos=None):
    """O preço dos produtos NÃO manuais acompanha o sugerido (custo + margem): recalcula sempre que algo mudou
    (filamento, insumo, energia, impressora, margem, lote…). Preço digitado à mão (preco_manual) nunca é alterado."""
    cfg, sd, mudou = get_config(s), saldos(s, "rolo"), False
    if produtos is None:
        produtos = s.exec(select(Produto).where(Produto.ativo, Produto.preco_manual == False)).all()  # noqa: E712
    for p in produtos:
        if p.preco_manual:
            continue
        novo = custo_produto(s, p, cfg, sd=sd)["preco"]
        if novo and novo != p.preco:  # sem receita/custo (preço 0) não sobrescreve
            p.preco = novo; s.add(p); mudou = True
    if mudou:
        s.commit()


def margem_real(p, custo):
    return (p.preco - custo) / p.preco * 100 if p.preco else Z


# ---------- Orçamento → Pedido
def criar_pedido_de_orcamento(s, o: Orcamento, prazo: date | None, do_estoque=False):
    cfg = get_config(s)
    ped = Pedido(orcamento_id=o.id, cliente_id=o.cliente_id, canal_id=o.canal_id, prazo=prazo,
                 desconto=o.desconto, frete=o.frete, observacoes=o.observacoes, do_estoque=do_estoque)
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


def qtds_prontas(s, ped):
    """{item_id: unidades que saem do estoque pronto}. Escolha dela no pedido ("atender com produto já pronto?"):
    SIM = tudo sai do produto pronto (não imprime, não gasta material); NÃO = tudo é impresso (só gasta material).
    Depois de iniciado, vale o que ficou gravado."""
    if ped.status != "aguardando":
        return {i.id: i.qtd_pronta for i in ped.itens}
    sim = ped.do_estoque and ped.tipo != "estoque"
    return {i.id: (i.qtd if sim and i.produto_id else 0) for i in ped.itens}


def plano_consumo(s, ped):
    """Filamentos necessários só para as unidades que serão IMPRESSAS (as já prontas em estoque não gastam material)."""
    pronta, need = qtds_prontas(s, ped), {}
    for it in ped.itens:
        if it.produto_id and it.qtd - pronta[it.id] > 0:
            lote = s.get(Produto, it.produto_id).lote_qtd or 1  # gramas do produto são do lote
            for pm in s.exec(select(ProdutoMaterial).where(ProdutoMaterial.produto_id == it.produto_id)):
                need[pm.material_id] = need.get(pm.material_id, Z) + pm.gramas * (it.qtd - pronta[it.id]) / lote
    sd, out = saldos(s, "rolo"), []
    for mid, g in need.items():
        rolos = [(r, sd.get(r.id, Z)) for r in s.exec(select(Rolo).where(Rolo.material_id == mid, Rolo.ativo).order_by(Rolo.id))
                 if sd.get(r.id, Z) > 0]
        out.append(dict(material=s.get(Material, mid), gramas=g, rolos=rolos))
    return out


def plano_insumos(s, ped):
    """Insumos necessários só para as unidades que serão impressas (qtd do produto é do lote → por unidade = qtd/lote)."""
    pronta, need = qtds_prontas(s, ped), {}
    for it in ped.itens:
        if it.produto_id and it.qtd - pronta[it.id] > 0:
            p = s.get(Produto, it.produto_id)
            for pi in p.insumos:
                need[pi.insumo_id] = need.get(pi.insumo_id, Z) + pi.qtd * (it.qtd - pronta[it.id]) / (p.lote_qtd or 1)
    sd = saldos(s, "insumo")
    return [dict(insumo=s.get(Insumo, i), qtd=q, saldo=sd.get(i, Z)) for i, q in need.items()]


# ---------- Pedido: máquina de estados
def mover_pedido(s, ped, para, consumos=(), insumos=()):
    """consumos: [(rolo_id, gramas)]; insumos: [(insumo_id, qtd)] — baixados ao iniciar a impressão.
    Retorna mensagem de erro ou None."""
    de, o = ped.status, ordem(ped)
    if para == "cancelado":
        if de in ("entregue", "cancelado") or (ped.tipo == "estoque" and de == "pronto"):
            return "Este pedido não pode mais ser cancelado."
    elif de == "cancelado":
        if para != "aguardando":
            return "Pedido cancelado só pode ser reaberto."
    elif de == "entregue" or de not in o or para not in o or abs(o.index(para) - o.index(de)) != 1:
        return "Movimento inválido."

    if de == "aguardando" and para == "imprimindo":
        pronta = qtds_prontas(s, ped)
        for it in ped.itens:  # fixa quanto sai do estoque pronto (reserva) e quanto será impresso
            it.qtd_pronta = pronta[it.id]; s.add(it)
        for rolo_id, g in consumos:
            if g > 0:
                baixar_rolo(s, rolo_id, g, "pedido", ped.id)
        for insumo_id, q in insumos:
            if q > 0:
                mov(s, "insumo", insumo_id, -q, "producao", "pedido", ped.id)
    if de == "acabamento" and para == "pronto":
        for it in ped.itens:
            if not it.produto_id:
                continue
            p = s.get(Produto, it.produto_id)
            if ped.tipo == "estoque":  # só reposição de estoque dá entrada no produto pronto
                mov(s, "produto", p.id, it.qtd, "producao", "pedido", ped.id)
            if p.impressora_id and it.qtd - it.qtd_pronta > 0:  # horas só das unidades impressas
                imp = s.get(Impressora, p.impressora_id)
                imp.horas_acumuladas += Decimal(p.tempo_min * (it.qtd - it.qtd_pronta)) / (60 * (p.lote_qtd or 1))
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
        if i.produto_id and i.qtd_pronta:  # só o que saiu do estoque pronto (o que foi impresso para o pedido nunca entrou nele)
            mov(s, "produto", i.produto_id, -i.qtd_pronta, "venda", "venda", v.id)
    _lancar_receita(s, v)
    return v


def venda_avulsa(s, cliente_id, canal_id, forma, data, linhas):
    """linhas: [(produto_id|None, descricao, qtd, preco_unit)]. A venda baixa o PRODUTO PRONTO (filamento e insumo
    são baixados na impressão, não na venda)."""
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
    for m in s.exec(select(MovEstoque).where(MovEstoque.ref_tipo == "venda", MovEstoque.ref_id == v.id,
                                             MovEstoque.motivo == "venda")):   # filamento/insumo já foram gastos
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
    """Marca/desmarca. Tarefa recorrente gera a próxima ocorrência UMA vez (e desmarcar desfaz, se ainda intocada)."""
    t.feita = not t.feita
    if t.recorrencia:
        prox = t.data + timedelta(days=1 if t.recorrencia == "diaria" else 7)
        q = select(Tarefa).where(Tarefa.titulo == t.titulo, Tarefa.recorrencia == t.recorrencia, Tarefa.data == prox,
                                 Tarefa.hora == t.hora, Tarefa.id != t.id)
        existente = s.exec(q).first()
        if t.feita and not existente:
            s.add(Tarefa(titulo=t.titulo, hora=t.hora, recorrencia=t.recorrencia, data=prox))
        elif not t.feita and existente and not existente.feita:
            s.delete(existente)
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
