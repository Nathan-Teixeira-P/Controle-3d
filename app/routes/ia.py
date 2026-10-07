"""Assistente local (Ollama). Só sugere texto/consulta; nunca altera dados. Consultas = funções fixas (sem SQL livre)."""
import json
import os
import re
from datetime import date

import httpx
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..empresa import EMPRESA
from ..models import Orcamento, Venda, VendaItem
from ..util import brl, page

router = APIRouter()
URL, MODEL = os.environ.get("OLLAMA_URL", "http://ollama:11434"), os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")


def llm(prompt: str, system="Você é o assistente de uma pequena empresa de impressão 3D. Responda em português do Brasil, de forma curta.") -> str:
    try:
        r = httpx.post(f"{URL}/api/chat", timeout=180, json={"model": MODEL, "stream": False,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except Exception:
        return ("⚠ IA indisponível. Suba o Ollama: `docker compose --profile ia up -d` e baixe o modelo: "
                f"`docker compose exec ollama ollama pull {MODEL}`.")


# --- consultas fixas que a IA pode escolher
def f_resumo_mes(s, **_):
    t = sv.totais_mes(s, date.today().replace(day=1), sv.add_months(date.today().replace(day=1)))
    return {k: str(t[k]) for k in ("n", "faturamento", "cmv", "taxas", "despesas", "lucro")}


def f_vendas_produto(s, produto="", **_):
    q = select(VendaItem).join(Venda).where(Venda.status == "ativa", VendaItem.descricao.ilike(f"%{produto}%"))
    its = s.exec(q).all()
    return dict(produto=produto, qtd=sum(i.qtd for i in its), receita=str(sum(i.qtd * i.preco_unit for i in its)),
                lucro=str(sum(i.qtd * (i.preco_unit - i.custo_unit) for i in its)))


def f_estoque_baixo(s, **_):
    return [dict(tipo=a["tipo"], item=a["nome"], saldo=str(a["saldo"]), minimo=str(a["minimo"]), situacao=a["status"]) for a in sv.estoque_baixo(s)]


def f_pedidos_producao(s, **_):
    return [dict(pedido=p.id, status=p.status, prazo=str(p.prazo)) for p in sv.dashboard(s, date.today())["em_producao"]]


FUNCS = {"resumo_mes": f_resumo_mes, "vendas_produto": f_vendas_produto, "estoque_baixo": f_estoque_baixo,
         "pedidos_producao": f_pedidos_producao}
AJUDA = ('Funções: resumo_mes (faturamento/lucro do mês atual), vendas_produto(produto) (quanto vendeu/lucrou de um produto), '
         'estoque_baixo, pedidos_producao. Responda SOMENTE um JSON como {"fn":"resumo_mes","args":{}} ou {"fn":"vendas_produto","args":{"produto":"chaveiro"}}.')


def perguntar(s, pergunta: str) -> str:
    esc = llm(pergunta, system=AJUDA)
    m = re.search(r"\{.*\}", esc, re.S)
    try:
        j = json.loads(m.group(0))
        fn = FUNCS[j["fn"]]
        dados = fn(s, **{k: str(v) for k, v in (j.get("args") or {}).items() if k == "produto"})
    except Exception:
        return esc if esc.startswith("⚠") else "Não consegui transformar a pergunta em uma consulta. Tente algo como: 'quanto lucrei este mês?'"
    return llm(f"Pergunta: {pergunta}\nDados (valores em reais): {json.dumps(dados, ensure_ascii=False)}\nResponda usando só esses dados.")


@router.get("/ia")
def ia(request: Request):
    return page(request, "ia.html", resposta="", pergunta="", modelo=MODEL)


@router.post("/ia/perguntar")
async def ia_perguntar(request: Request, s: Session = Depends(get_session)):
    q = (await request.form())["pergunta"]
    return page(request, "ia.html", resposta=perguntar(s, q), pergunta=q, modelo=MODEL)


@router.post("/ia/resumo")
def ia_resumo(request: Request, s: Session = Depends(get_session)):
    d = f_resumo_mes(s)
    r = llm(f"Escreva um resumo curto e amigável do mês para a dona do negócio com estes números em reais: {json.dumps(d)}")
    return page(request, "ia.html", resposta=r, pergunta="Resumo do mês", modelo=MODEL)


@router.post("/ia/mensagem/{id}")
def ia_mensagem(id: int, s: Session = Depends(get_session)):
    from fastapi.responses import PlainTextResponse
    o = s.get(Orcamento, id)
    itens = "; ".join(f"{i.qtd}x {i.descricao}" for i in o.itens)
    return PlainTextResponse(llm(
        f"Escreva uma mensagem curta e simpática de WhatsApp enviando o orçamento nº {o.id:04d} para {o.cliente.nome}. "
        f"Itens: {itens}. Total {brl(o.total)}. Validade até {o.valido_ate:%d/%m/%Y}. Assine como {EMPRESA['nome']}."))
