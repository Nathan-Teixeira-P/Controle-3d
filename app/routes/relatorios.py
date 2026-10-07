import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import (Canal, Cliente, Insumo, Lancamento, MovEstoque, Pedido, Produto, Rolo, Venda, VendaItem)
from ..util import dec, page

router = APIRouter()
Z = Decimal(0)

NOMES = {"vendas": "Vendas", "custos": "Custos", "lucratividade": "Lucratividade", "estoque": "Estoque",
         "produtos": "Produtos", "clientes": "Clientes", "producao": "Produção"}


def _pct(a, b):
    return dec(a / b * 100, 1) + "%" if b else "-"


def r_vendas(s, ini, fim, **_):
    vs = s.exec(select(Venda).where(Venda.status == "ativa", Venda.data >= ini, Venda.data <= fim).order_by(Venda.data)).all()
    return (["Data", "Venda", "Cliente", "Canal", "Pagamento", "Total", "Custo", "Taxa", "Lucro"],
            [[v.data.strftime("%d/%m/%Y"), v.id, v.cliente.nome if v.cliente else "Balcão", v.canal.nome if v.canal else "",
              v.forma_pagamento, v.total, v.custo, v.taxa, v.lucro] for v in vs])


def r_custos(s, ini, fim, **_):
    vs = s.exec(select(Venda).where(Venda.status == "ativa", Venda.data >= ini, Venda.data <= fim)).all()
    rows = [["Custo dos produtos vendidos (material, energia, mão de obra...)", "variável", sum((v.custo for v in vs), Z)],
            ["Taxas de canais", "variável", sum((v.taxa for v in vs), Z)]]
    ds = {}
    for l in s.exec(select(Lancamento).where(Lancamento.tipo == "despesa", Lancamento.cancelado == False,  # noqa: E712
                                             Lancamento.pago_em >= ini, Lancamento.pago_em <= fim)):
        k = (l.categoria or "Sem categoria", l.natureza)
        ds[k] = ds.get(k, Z) + l.valor
    rows += [[c, n, v] for (c, n), v in sorted(ds.items())]
    return ["Custo", "Natureza", "Total"], rows


def r_lucratividade(s, ini, fim, por="produto", **_):
    vs = s.exec(select(Venda).where(Venda.status == "ativa", Venda.data >= ini, Venda.data <= fim)).all()
    acc = {}
    for v in vs:
        if por == "canal":
            k = v.canal.nome if v.canal else "Sem canal"
            a = acc.setdefault(k, [0, Z, Z, Z]); a[0] += len(v.itens); a[1] += v.total; a[2] += v.custo; a[3] += v.taxa
        else:
            for i in v.itens:
                a = acc.setdefault(i.descricao, [0, Z, Z, Z])
                a[0] += i.qtd; a[1] += i.qtd * i.preco_unit; a[2] += i.qtd * i.custo_unit
    rows = [[k, a[0], a[1], a[2], a[3], a[1] - a[2] - a[3], _pct(a[1] - a[2] - a[3], a[1])] for k, a in sorted(acc.items(), key=lambda x: -(x[1][1] - x[1][2]))]
    return [("Canal" if por == "canal" else "Produto"), "Qtd", "Receita", "Custo", "Taxas", "Lucro", "Margem"], rows


def r_estoque(s, ini, fim, **_):
    rows, sd = [], sv.saldos(s, "rolo")
    for r in s.exec(select(Rolo).where(Rolo.ativo)):
        sl = sd.get(r.id, Z)
        rows.append(["Filamento", f"Rolo #{r.id} {r.material.nome}", sl, "g", r.material.minimo_g, sl * r.custo_g])
    si = sv.saldos(s, "insumo")
    for i in s.exec(select(Insumo).where(Insumo.ativo)):
        rows.append(["Insumo", i.nome, si.get(i.id, Z), i.unidade, i.minimo, si.get(i.id, Z) * i.custo_unit])
    sp = sv.saldos(s, "produto")
    for p in s.exec(select(Produto).where(Produto.ativo)):
        c = sv.custo_produto(s, p)["custo"]
        rows.append(["Produto pronto", p.nome, sp.get(p.id, Z), "un", p.minimo, sp.get(p.id, Z) * c])
    return ["Tipo", "Item", "Saldo", "Un.", "Mínimo", "Valor em estoque (custo)"], rows


def r_produtos(s, ini, fim, **_):
    sv.atualizar_precos(s)
    vend = {}
    for i in s.exec(select(VendaItem).join(Venda).where(Venda.status == "ativa", Venda.data >= ini, Venda.data <= fim)):
        if i.produto_id:
            vend[i.produto_id] = vend.get(i.produto_id, 0) + i.qtd
    rows = []
    for p in s.exec(select(Produto).where(Produto.ativo).order_by(Produto.nome)):
        c = sv.custo_produto(s, p)["custo"]
        rows.append([p.sku, p.nome, p.categoria, c, p.preco, _pct(p.preco - c, p.preco), dec(p.margem_pct, 1) + "%", vend.get(p.id, 0)])
    return ["SKU", "Produto", "Categoria", "Custo", "Preço", "Margem real", "Margem alvo", "Vendidos no período"], rows


def r_clientes(s, ini, fim, **_):
    acc = {}
    for v in s.exec(select(Venda).where(Venda.status == "ativa", Venda.data >= ini, Venda.data <= fim)):
        k = v.cliente.nome if v.cliente else "Balcão"
        a = acc.setdefault(k, [0, Z, v.data]); a[0] += 1; a[1] += v.total; a[2] = max(a[2], v.data)
    return (["Cliente", "Nº de vendas", "Total comprado", "Última compra"],
            [[k, a[0], a[1], a[2].strftime("%d/%m/%Y")] for k, a in sorted(acc.items(), key=lambda x: -x[1][1])])


def r_producao(s, ini, fim, **_):
    rows = []
    falhas = {}
    for m in s.exec(select(MovEstoque).where(MovEstoque.motivo == "falha", MovEstoque.ref_tipo == "pedido")):
        falhas[m.ref_id] = falhas.get(m.ref_id, Z) - m.delta
    for p in s.exec(select(Pedido).where(Pedido.criado_em >= ini, Pedido.criado_em < date.fromordinal(fim.toordinal() + 1)).order_by(Pedido.id)):
        horas = sum((Decimal(s.get(Produto, i.produto_id).tempo_min * i.qtd) / (60 * (s.get(Produto, i.produto_id).lote_qtd or 1)) for i in p.itens if i.produto_id), Z)
        rows.append([p.id, p.tipo, p.cliente.nome if p.cliente else "Estoque", p.status, p.criado_em.strftime("%d/%m/%Y"),
                     p.prazo.strftime("%d/%m/%Y") if p.prazo else "", sum(i.qtd for i in p.itens), horas, falhas.get(p.id, Z), p.custo_extra])
    return ["Pedido", "Tipo", "Cliente", "Status", "Criado", "Prazo", "Peças", "Horas previstas", "Falhas (g)", "Custo das falhas"], rows


REL = {"vendas": r_vendas, "custos": r_custos, "lucratividade": r_lucratividade, "estoque": r_estoque,
       "produtos": r_produtos, "clientes": r_clientes, "producao": r_producao}


def _txt(v):
    return dec(v) if isinstance(v, Decimal) else v


@router.get("/relatorios")
def indice(request: Request):
    return page(request, "relatorios.html", nomes=NOMES)


@router.get("/relatorios/{nome}")
def ver(nome: str, request: Request, ini: str = "", fim: str = "", por: str = "produto", fmt: str = "html",
        s: Session = Depends(get_session)):
    hoje = date.today()
    i = date.fromisoformat(ini) if ini else hoje.replace(day=1)
    f = date.fromisoformat(fim) if fim else hoje
    cab, rows = REL[nome](s, i, f, por=por)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(cab)
        w.writerows([[_txt(c) for c in r] for r in rows])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{nome}.csv"'})
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.append(cab)
        for r in rows:
            ws.append([float(c) if isinstance(c, Decimal) else c for c in r])
        buf = io.BytesIO(); wb.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{nome}.xlsx"'})
    return page(request, "relatorio.html", nome=nome, titulo=NOMES[nome], cab=cab, rows=[[_txt(c) for c in r] for r in rows],
                ini=i, fim=f, por=por)
