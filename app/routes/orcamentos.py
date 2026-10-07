from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..empresa import EMPRESA
from ..models import Canal, Cliente, Item, Orcamento, Produto
from ..util import BASE, D, brl, page, paginate, tpl, whatsapp_link

router = APIRouter()


def _ctx_form(s):
    sv.atualizar_precos(s)
    return dict(clientes=s.exec(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all(),
                canais=s.exec(select(Canal).where(Canal.ativo)).all(),
                produtos=s.exec(select(Produto).where(Produto.ativo).order_by(Produto.nome)).all())


@router.get("/orcamentos")
def lista(request: Request, q: str = "", status: str = "", pagina: int = 1, s: Session = Depends(get_session)):
    st = select(Orcamento).join(Cliente).order_by(Orcamento.id.desc())
    if q:
        st = st.where(Cliente.nome.ilike(f"%{q}%"))
    if status and status != "expirado":
        st = st.where(Orcamento.status == status)
    pg = paginate(s, st, pagina)
    if status == "expirado":
        pg.items = [o for o in pg.items if o.status_exib == "expirado"]
    return page(request, "orcamentos.html", pg=pg, q=q, status=status)


@router.get("/orcamentos/novo")
def novo(request: Request, cliente_id: int = 0, s: Session = Depends(get_session)):
    return page(request, "orcamento_form.html", o=None, cliente_id=cliente_id, **_ctx_form(s))


@router.get("/orcamentos/{id}/editar")
def editar(id: int, request: Request, s: Session = Depends(get_session)):
    return page(request, "orcamento_form.html", o=s.get(Orcamento, id), cliente_id=0, **_ctx_form(s))


@router.post("/orcamentos")
async def salvar(request: Request, s: Session = Depends(get_session)):
    sv.atualizar_precos(s)
    f = await request.form()
    o = s.get(Orcamento, int(f["id"])) if f.get("id") else Orcamento(cliente_id=0)
    o.cliente_id = int(f["cliente_id"])
    o.canal_id = int(f["canal_id"]) if f.get("canal_id") else None
    o.validade_dias = int(D(f.get("validade_dias"), 7))
    o.desconto, o.frete = D(f.get("desconto")), D(f.get("frete"))
    o.prazo, o.pagamento, o.observacoes = f["prazo"], f["pagamento"], f["observacoes"]
    cfg = sv.get_config(s)
    itens = []
    for pid, d, p, q, v, c in zip(f.getlist("produto_id"), f.getlist("descricao"), f.getlist("personalizacao"),
                                  f.getlist("qtd"), f.getlist("preco"), f.getlist("custo")):
        pid = int(pid) if pid else None
        if not (d.strip() or pid):
            continue
        prod = s.get(Produto, pid) if pid else None
        itens.append(Item(produto_id=pid, descricao=d.strip() or prod.nome, personalizacao=p, qtd=int(D(q, 1)),
                          preco_unit=D(v) if v.strip() else (prod.preco if prod else D(0)), custo_unit=D(c)))
    o.itens = itens
    s.add(o); s.commit()
    return RedirectResponse(f"/orcamentos/{o.id}", 303)


def _doc(request, o, pdf, cfg):
    return tpl.get_template("orcamento_doc.html").render(
        request=request, o=o, pdf=pdf, cfg=cfg, empresa=EMPRESA, brl=brl,
        logo=f"file://{BASE}/static/logo.png" if pdf else "/static/logo.png")


@router.get("/orcamentos/{id}")
def ver(id: int, request: Request, s: Session = Depends(get_session)):
    o = s.get(Orcamento, id)
    cfg = sv.get_config(s)
    txt = (f"Olá {o.cliente.nome.split()[0]}! Segue o orçamento nº {o.id:04d} da {EMPRESA['nome']}, "
           f"no valor de {brl(o.total)}, válido até {o.valido_ate.strftime('%d/%m/%Y')}. Qualquer dúvida é só chamar!")
    return Response(tpl.get_template("orcamento_ver.html").render(
        request=request, o=o, doc=_doc(request, o, False, cfg), wa=whatsapp_link(o.cliente.telefone, txt),
        hoje=date.today(), prazo=date.today() + timedelta(days=5)), media_type="text/html")


@router.get("/orcamentos/{id}/doc")
def doc_puro(id: int, request: Request, s: Session = Depends(get_session)):
    return Response(_doc(request, s.get(Orcamento, id), False, sv.get_config(s)), media_type="text/html")


@router.get("/orcamentos/{id}/pdf")
def pdf(id: int, request: Request, download: int = 0, s: Session = Depends(get_session)):
    from weasyprint import HTML
    o = s.get(Orcamento, id)
    dados = HTML(string=_doc(request, o, True, sv.get_config(s))).write_pdf()
    disp = "attachment" if download else "inline"
    return Response(dados, media_type="application/pdf", headers={"Content-Disposition": f'{disp}; filename="orcamento-{id:04d}.pdf"'})


@router.post("/orcamentos/{id}/aprovar")
async def aprovar(id: int, request: Request, s: Session = Depends(get_session)):
    o = s.get(Orcamento, id)
    if o.status != "aberto" or not o.itens:
        return RedirectResponse(f"/orcamentos/{id}", 303)
    f = await request.form()
    ped = sv.criar_pedido_de_orcamento(s, o, date.fromisoformat(f["prazo"]) if f.get("prazo") else None, f.get("do_estoque") == "1")
    return RedirectResponse(f"/pedidos/{ped.id}", 303)


@router.post("/orcamentos/{id}/status")
async def status(id: int, request: Request, s: Session = Depends(get_session)):
    o = s.get(Orcamento, id)
    novo = (await request.form())["status"]
    if o.status != "aprovado" and novo in ("aberto", "recusado"):
        o.status = novo; s.add(o); s.commit()
    return RedirectResponse(f"/orcamentos/{id}", 303)


@router.post("/orcamentos/{id}/duplicar")
def duplicar(id: int, s: Session = Depends(get_session)):
    o = s.get(Orcamento, id)
    n = Orcamento(cliente_id=o.cliente_id, canal_id=o.canal_id, validade_dias=o.validade_dias, desconto=o.desconto,
                  frete=o.frete, prazo=o.prazo, pagamento=o.pagamento, observacoes=o.observacoes,
                  itens=[Item(produto_id=i.produto_id, descricao=i.descricao, personalizacao=i.personalizacao, qtd=i.qtd,
                              preco_unit=i.preco_unit, custo_unit=i.custo_unit) for i in o.itens])
    s.add(n); s.commit()
    return RedirectResponse(f"/orcamentos/{n.id}/editar", 303)


@router.post("/orcamentos/{id}/excluir")
def excluir(id: int, s: Session = Depends(get_session)):
    o = s.get(Orcamento, id)
    if o.status != "aprovado":
        s.delete(o); s.commit()
        return RedirectResponse("/orcamentos", 303)
    return RedirectResponse(f"/orcamentos/{id}", 303)
