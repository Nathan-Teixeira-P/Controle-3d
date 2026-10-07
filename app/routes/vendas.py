from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import Canal, Cliente, Produto, Venda
from ..util import D, page, paginate

router = APIRouter()


@router.get("/vendas")
def lista(request: Request, mes: str = "", pagina: int = 1, s: Session = Depends(get_session)):
    st = select(Venda).order_by(Venda.data.desc(), Venda.id.desc())
    if mes:
        ini = date.fromisoformat(mes + "-01")
        st = st.where(Venda.data >= ini, Venda.data < sv.add_months(ini))
    pg = paginate(s, st, pagina)
    tot = sum((v.total for v in pg.items if v.status == "ativa"), sv.Z)
    return page(request, "vendas.html", pg=pg, mes=mes, tot=tot)


@router.get("/vendas/nova")
def nova(request: Request, s: Session = Depends(get_session)):
    return page(request, "venda_form.html", hoje=date.today(),
                clientes=s.exec(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all(),
                canais=s.exec(select(Canal).where(Canal.ativo)).all(),
                produtos=s.exec(select(Produto).where(Produto.ativo).order_by(Produto.nome)).all())


@router.post("/vendas")
async def criar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    linhas = []
    for pid, d, q, v in zip(f.getlist("produto_id"), f.getlist("descricao"), f.getlist("qtd"), f.getlist("preco")):
        pid = int(pid) if pid else None
        if pid or d.strip():
            p = s.get(Produto, pid) if pid else None
            linhas.append((pid, d.strip(), int(D(q, 1)), D(v) if v.strip() else (p.preco if p else D(0))))
    if not linhas:
        return RedirectResponse("/vendas/nova", 303)
    v = sv.venda_avulsa(s, int(f["cliente_id"]) if f.get("cliente_id") else None, int(f["canal_id"]) if f.get("canal_id") else None,
                        f.get("forma_pagamento", "PIX"), date.fromisoformat(f["data"]), linhas)
    return RedirectResponse(f"/vendas/{v.id}", 303)


@router.get("/vendas/{id}")
def ver(id: int, request: Request, s: Session = Depends(get_session)):
    return page(request, "venda.html", v=s.get(Venda, id))


@router.post("/vendas/{id}/estornar")
def estornar(id: int, s: Session = Depends(get_session)):
    sv.estornar_venda(s, s.get(Venda, id))
    return RedirectResponse(f"/vendas/{id}", 303)
