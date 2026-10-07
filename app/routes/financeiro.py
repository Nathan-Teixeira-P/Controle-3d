from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import Lancamento
from ..util import D, page

router = APIRouter()


@router.get("/financeiro")
def financeiro(request: Request, mes: str = "", s: Session = Depends(get_session)):
    hoje = date.today()
    ini = date.fromisoformat(mes + "-01") if mes else hoje.replace(day=1)
    fim = sv.add_months(ini)
    ls = s.exec(select(Lancamento).where(Lancamento.cancelado == False, Lancamento.vencimento >= ini,  # noqa: E712
                                         Lancamento.vencimento < fim).order_by(Lancamento.vencimento, Lancamento.id)).all()
    soma = lambda tipo, pago: sum((l.valor for l in ls if l.tipo == tipo and (l.pago_em is not None) == pago), sv.Z)
    abertos = s.exec(select(Lancamento).where(Lancamento.cancelado == False, Lancamento.pago_em == None)  # noqa: E711,E712
                     .order_by(Lancamento.vencimento)).all()
    # últimos 6 meses (por data de pagamento)
    meses = []
    for k in range(5, -1, -1):
        a = sv.add_months(ini, -k)
        pagos = s.exec(select(Lancamento).where(Lancamento.cancelado == False, Lancamento.pago_em >= a,  # noqa: E712
                                                Lancamento.pago_em < sv.add_months(a))).all()
        r = sum((l.valor for l in pagos if l.tipo == "receita"), sv.Z)
        d = sum((l.valor for l in pagos if l.tipo == "despesa"), sv.Z)
        meses.append(dict(mes=a, receita=r, despesa=d))
    topo = max([max(m["receita"], m["despesa"]) for m in meses] + [D(1)])
    cats = sorted({c for c in s.exec(select(Lancamento.categoria).distinct()).all() if c} | {"Aluguel", "Energia", "Internet", "Material", "Embalagens", "Manutenção", "Impostos"})
    return page(request, "financeiro.html", ls=ls, ini=ini, ant=sv.add_months(ini, -1), prox=fim, hoje=hoje, meses=meses, topo=topo,
                rec_pago=soma("receita", True), desp_pago=soma("despesa", True), rec_aberto=soma("receita", False),
                desp_aberto=soma("despesa", False), abertos=abertos, cats=cats,
                fixas=sum((l.valor for l in ls if l.tipo == "despesa" and l.natureza == "fixa"), sv.Z),
                variaveis=sum((l.valor for l in ls if l.tipo == "despesa" and l.natureza == "variavel"), sv.Z))


@router.post("/lancamentos")
async def criar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    l = Lancamento(tipo=f["tipo"], categoria=f.get("categoria", "").strip(), natureza=f.get("natureza", "variavel"),
                   descricao=f.get("descricao", "").strip(), valor=D(f["valor"]), vencimento=date.fromisoformat(f["vencimento"]),
                   recorrencia=f.get("recorrencia", ""))
    s.add(l); s.flush()
    if f.get("pago") == "1":
        sv.pagar(s, l, l.vencimento)
    s.commit()
    return RedirectResponse(f"/financeiro?mes={l.vencimento:%Y-%m}", 303)


@router.post("/lancamentos/{id}/pagar")
def pagar(id: int, s: Session = Depends(get_session)):
    l = s.get(Lancamento, id)
    sv.pagar(s, l, date.today())
    return RedirectResponse(f"/financeiro?mes={l.vencimento:%Y-%m}", 303)


@router.post("/lancamentos/{id}/desfazer")
def desfazer(id: int, s: Session = Depends(get_session)):
    l = s.get(Lancamento, id)
    if not l.venda_id:
        l.pago_em = None; s.add(l); s.commit()
    return RedirectResponse(f"/financeiro?mes={l.vencimento:%Y-%m}", 303)


@router.post("/lancamentos/{id}/cancelar")
def cancelar(id: int, s: Session = Depends(get_session)):
    l = s.get(Lancamento, id)
    if not l.venda_id:  # receita de venda só some estornando a venda
        l.cancelado = True; s.add(l); s.commit()
    return RedirectResponse(f"/financeiro?mes={l.vencimento:%Y-%m}", 303)
