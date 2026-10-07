from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import Canal, Cliente, Produto, Venda
from ..util import D, barreira, page, paginate

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
    sv.atualizar_precos(s)
    return page(request, "venda_form.html", hoje=date.today(),
                clientes=s.exec(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all(),
                canais=s.exec(select(Canal).where(Canal.ativo)).all(),
                produtos=s.exec(select(Produto).where(Produto.ativo).order_by(Produto.nome)).all())


@router.post("/vendas")
async def criar(request: Request, s: Session = Depends(get_session)):
    sv.atualizar_precos(s)
    f = await request.form()
    linhas = []
    for pid, d, q, v in zip(f.getlist("produto_id"), f.getlist("descricao"), f.getlist("qtd"), f.getlist("preco")):
        pid = int(pid) if pid else None
        if pid or d.strip():
            p = s.get(Produto, pid) if pid else None
            linhas.append((pid, d.strip(), int(D(q, 1)), D(v) if v.strip() else (p.preco if p else D(0))))
    if not linhas:
        return RedirectResponse("/vendas/nova", 303)
    faltas = sv.faltas_pronto(s, [(l[0], l[2]) for l in linhas])
    if faltas and not f.get("forcar"):
        return barreira(request, faltas, "/vendas", f, "/vendas/nova", "registrar a venda")
    v = sv.venda_avulsa(s, int(f["cliente_id"]) if f.get("cliente_id") else None, int(f["canal_id"]) if f.get("canal_id") else None,
                        f.get("forma_pagamento", "PIX"), date.fromisoformat(f["data"]), linhas)
    neg = [s.get(Produto, pid).nome for pid in {l[0] for l in linhas if l[0]} if sv.saldo(s, "produto", pid) < 0]
    aviso = f"?msg=Atenção: estoque pronto negativo ({', '.join(neg)}). Registre a produção ou ajuste o estoque." if neg else ""
    return RedirectResponse(f"/vendas/{v.id}{aviso}", 303)


@router.get("/vendas/{id}")
def ver(id: int, request: Request, s: Session = Depends(get_session)):
    return page(request, "venda.html", v=s.get(Venda, id), msg=request.query_params.get("msg", ""))


@router.post("/vendas/{id}/estornar")
def estornar(id: int, s: Session = Depends(get_session)):
    sv.estornar_venda(s, s.get(Venda, id))
    return RedirectResponse(f"/vendas/{id}", 303)
