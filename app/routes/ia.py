"""Assistente local (Ollama). Só sugere texto/consulta; nunca altera dados. Consultas = funções fixas (sem SQL livre)."""
import os
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..empresa import EMPRESA
from ..models import Insumo, Lancamento, Material, MovEstoque, Orcamento, Pedido, Produto, Rolo, Venda, VendaItem
from ..util import brl, page

router = APIRouter()
URL, MODEL = os.environ.get("OLLAMA_URL", "http://ollama:11434"), os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")


def llm(prompt: str, system="Você é o assistente de uma pequena empresa de impressão 3D. Responda em português do Brasil, de forma curta.") -> str:
    try:
        r = httpx.post(f"{URL}/api/chat", timeout=180, json={"model": MODEL, "stream": False, "options": {"num_ctx": 8192},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except Exception:
        return ("⚠ IA indisponível. Suba o Ollama: `docker compose --profile ia up -d` e baixe o modelo: "
                f"`docker compose exec ollama ollama pull {MODEL}`.")


# --- a IA recebe um retrato do negócio (só leitura) e responde em cima dele; sem roteamento de funções
def _r(x):
    return f"{Decimal(x):.2f}"


def contexto(s, hoje=None) -> str:
    hoje = hoje or date.today()
    m0 = hoje.replace(day=1)
    L = [f"Hoje: {hoje:%d/%m/%Y}. Valores em reais."]
    # vendas mês a mês (12 meses) e acumulado dos últimos 3
    L.append("VENDAS POR MÊS (n vendas | faturamento | custo | taxas | despesas | lucro):")
    acc = dict(n=0, faturamento=0, lucro=0)
    for k in range(11, -1, -1):
        ini = sv.add_months(m0, -k)
        t = sv.totais_mes(s, ini, sv.add_months(ini))
        L.append(f"- {ini:%m/%Y}: {t['n']} | {_r(t['faturamento'])} | {_r(t['cmv'])} | {_r(t['taxas'])} | {_r(t['despesas'])} | {_r(t['lucro'])}")
        if k < 3:
            for c in acc:
                acc[c] += t[c]
    L.append(f"ÚLTIMOS 3 MESES (inclui o atual): {acc['n']} vendas, faturamento {_r(acc['faturamento'])}, lucro {_r(acc['lucro'])}")

    # produtos mais vendidos (12 meses)
    ini12 = sv.add_months(m0, -11)
    its = s.exec(select(VendaItem).join(Venda).where(Venda.status == "ativa", Venda.data >= ini12)).all()
    prod = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    for i in its:
        p = prod[i.descricao]
        p[0] += i.qtd; p[1] += i.qtd * i.preco_unit; p[2] += i.qtd * (i.preco_unit - i.custo_unit)
    L.append("PRODUTOS MAIS VENDIDOS, 12 meses (qtd | receita | lucro):")
    L += [f"- {n}: {q} | {_r(r)} | {_r(l)}" for n, (q, r, l) in sorted(prod.items(), key=lambda x: -x[1][0])[:15]] or ["- (sem vendas)"]

    # clientes e canais
    vs = s.exec(select(Venda).where(Venda.status == "ativa", Venda.data >= ini12)).all()
    for titulo, f in (("MELHORES CLIENTES, 12 meses", lambda v: v.cliente.nome if v.cliente else "Sem cliente"),
                      ("VENDAS POR CANAL, 12 meses", lambda v: v.canal.nome if v.canal else "Sem canal")):
        d = defaultdict(lambda: [0, Decimal(0)])
        for v in vs:
            d[f(v)][0] += 1; d[f(v)][1] += v.total
        L.append(f"{titulo} (vendas | total):")
        L += [f"- {n}: {q} | {_r(t)}" for n, (q, t) in sorted(d.items(), key=lambda x: -x[1][1])[:8]] or ["- (nada)"]

    # consumo de filamento e insumos (3 meses e 12 meses, saídas do estoque por produção/falha)
    mats = {m.id: m.nome for m in s.exec(select(Material)).all()}
    rolos = {r.id: mats.get(r.material_id, "?") for r in s.exec(select(Rolo)).all()}
    ins = {i.id: f"{i.nome} ({i.unidade})" for i in s.exec(select(Insumo)).all()}
    movs = s.exec(select(MovEstoque).where(MovEstoque.delta < 0, MovEstoque.motivo.in_(["producao", "falha"]),
                                           MovEstoque.item_tipo.in_(["rolo", "insumo"]), MovEstoque.em >= datetime.combine(ini12, time()))).all()
    uso = {"rolo": defaultdict(Decimal), "insumo": defaultdict(Decimal)}
    for mv in movs:
        nome = rolos.get(mv.item_id, "?") if mv.item_tipo == "rolo" else ins.get(mv.item_id, "?")
        uso[mv.item_tipo][nome] += -mv.delta
    L.append("FILAMENTOS MAIS USADOS, 12 meses (gramas):")
    L += [f"- {n}: {_r(g)}" for n, g in sorted(uso["rolo"].items(), key=lambda x: -x[1])[:10]] or ["- (nada)"]
    L.append("INSUMOS MAIS USADOS, 12 meses (quantidade):")
    L += [f"- {n}: {_r(g)}" for n, g in sorted(uso["insumo"].items(), key=lambda x: -x[1])[:10]] or ["- (nada)"]

    # estoque, pedidos, orçamentos, financeiro
    L.append("ESTOQUE BAIXO (saldo | mínimo):")
    L += [f"- {a['tipo']} {a['nome']}: {a['saldo']} | {a['minimo']} ({a['status']})" for a in sv.estoque_baixo(s)] or ["- (nada)"]
    ped = s.exec(select(Pedido).where(Pedido.status.in_(["aguardando", "imprimindo", "acabamento", "pronto", "enviado"]))).all()
    L.append("PEDIDOS EM ANDAMENTO:")
    L += [f"- #{p.id} {p.status}, cliente {p.cliente.nome if p.cliente else '-'}, total {_r(p.total)}, prazo {p.prazo or '-'}"
          f"{' ATRASADO' if p.atrasado else ''}" for p in ped[:20]] or ["- (nenhum)"]
    ab = s.exec(select(Orcamento).where(Orcamento.status == "aberto")).all()
    L.append(f"ORÇAMENTOS ABERTOS: {len(ab)}, soma {_r(sum((o.total for o in ab), Decimal(0)))}")
    lc = s.exec(select(Lancamento).where(Lancamento.cancelado == False, Lancamento.pago_em == None)).all()  # noqa: E711,E712
    for tp, nm in (("receita", "A RECEBER"), ("despesa", "A PAGAR")):
        x = [l for l in lc if l.tipo == tp]
        L.append(f"{nm}: {len(x)} lançamentos, soma {_r(sum((l.valor for l in x), Decimal(0)))}, "
                 f"vencidos {sum(1 for l in x if l.vencimento < hoje)}")
    L.append(f"PRODUTOS ATIVOS: {len(s.exec(select(Produto).where(Produto.ativo == True)).all())}")  # noqa: E712
    return "\n".join(L)


SISTEMA = ("Você é o assistente de uma pequena empresa de impressão 3D. Responda em português do Brasil, curto e direto, "
           "usando SOMENTE os dados abaixo (não invente números). Se o dado não estiver aí, diga que não tem.\n\nDADOS DO SISTEMA:\n")


def perguntar(s, pergunta: str) -> str:
    return llm(pergunta, system=SISTEMA + contexto(s))


@router.get("/ia")
def ia(request: Request):
    return page(request, "ia.html", resposta="", pergunta="", modelo=MODEL)


@router.post("/ia/perguntar")
async def ia_perguntar(request: Request, s: Session = Depends(get_session)):
    q = (await request.form())["pergunta"]
    return page(request, "ia.html", resposta=perguntar(s, q), pergunta=q, modelo=MODEL)


@router.post("/ia/resumo")
def ia_resumo(request: Request, s: Session = Depends(get_session)):
    r = llm("Escreva um resumo curto e amigável do mês atual para a dona do negócio: vendas, lucro, o que mais saiu, "
            "o que está acabando e o que precisa de atenção.", system=SISTEMA + contexto(s))
    return page(request, "ia.html", resposta=r, pergunta="Resumo do mês", modelo=MODEL)


@router.post("/ia/mensagem/{id}")
def ia_mensagem(id: int, s: Session = Depends(get_session)):
    from fastapi.responses import PlainTextResponse
    o = s.get(Orcamento, id)
    itens = "; ".join(f"{i.qtd}x {i.descricao}" for i in o.itens)
    return PlainTextResponse(llm(
        f"Escreva uma mensagem curta e simpática de WhatsApp enviando o orçamento nº {o.id:04d} para {o.cliente.nome}. "
        f"Itens: {itens}. Total {brl(o.total)}. Validade até {o.valido_ate:%d/%m/%Y}. Assine como {EMPRESA['nome']}."))
