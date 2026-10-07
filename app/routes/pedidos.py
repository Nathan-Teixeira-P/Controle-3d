from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import Canal, Cliente, Pedido, PedidoItem, PedidoHistorico, Produto, Venda
from ..util import D, barreira, page

router = APIRouter()
COLUNAS = sv.ORDEM + ["cancelado"]


@router.get("/pedidos")
def kanban(request: Request, tipo: str = "", s: Session = Depends(get_session)):
    st = select(Pedido).order_by(Pedido.prazo, Pedido.id)
    if tipo:
        st = st.where(Pedido.tipo == tipo)
    ps = s.exec(st).all()
    # entregues/cancelados antigos poluem o quadro: mostra só os 15 mais recentes de cada
    col = {c: [p for p in ps if p.status == c] for c in COLUNAS}
    for c in ("entregue", "cancelado"):
        col[c] = sorted(col[c], key=lambda p: -p.id)[:15]
    return page(request, "pedidos.html", col=col, colunas=COLUNAS, tipo=tipo, ordem=sv.ORDEM)


@router.get("/pedidos/novo")
def novo(request: Request, s: Session = Depends(get_session)):
    sv.atualizar_precos(s)
    return page(request, "pedido_form.html", clientes=s.exec(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all(),
                canais=s.exec(select(Canal).where(Canal.ativo)).all(),
                produtos=s.exec(select(Produto).where(Produto.ativo).order_by(Produto.nome)).all())


@router.post("/pedidos")
async def criar(request: Request, s: Session = Depends(get_session)):
    """Pedido direto (sem orçamento) ou de reposição de estoque."""
    sv.atualizar_precos(s)
    f = await request.form()
    cfg, tipo = sv.get_config(s), f.get("tipo", "cliente")
    ped = Pedido(tipo=tipo, cliente_id=int(f["cliente_id"]) if f.get("cliente_id") and tipo == "cliente" else None,
                 canal_id=int(f["canal_id"]) if f.get("canal_id") else None, observacoes=f.get("observacoes", ""),
                 prazo=date.fromisoformat(f["prazo"]) if f.get("prazo") else None,
                 do_estoque=f.get("do_estoque") == "1" and tipo == "cliente", desconto=D(f.get("desconto")), frete=D(f.get("frete")))
    s.add(ped); s.flush()
    for pid, d, q, v in zip(f.getlist("produto_id"), f.getlist("descricao"), f.getlist("qtd"), f.getlist("preco")):
        pid = int(pid) if pid else None
        if not (pid or d.strip()):
            continue
        p = s.get(Produto, pid) if pid else None
        custo = sv.custo_produto(s, p, cfg)["custo"] if p else D(0)
        s.add(PedidoItem(pedido_id=ped.id, produto_id=pid, descricao=d.strip() or p.nome, qtd=int(D(q, 1)),
                         preco_unit=D(v) if v.strip() else (p.preco if p else D(0)), custo_unit=custo))
    s.add(PedidoHistorico(pedido_id=ped.id, de="", para="aguardando"))
    s.commit()
    return RedirectResponse(f"/pedidos/{ped.id}", 303)


@router.get("/pedidos/{id}")
def ver(id: int, request: Request, s: Session = Depends(get_session)):
    ped = s.get(Pedido, id)
    venda = s.exec(select(Venda).where(Venda.pedido_id == id, Venda.status == "ativa")).first()
    plano = sv.plano_consumo(s, ped)
    return page(request, "pedido.html", p=ped, venda=venda, ordem=sv.ordem(ped), plano=plano, pins=sv.plano_insumos(s, ped), pronta=sv.qtds_prontas(s, ped),
                rolos=[r for pl in plano for r in pl["rolos"]], msg=request.query_params.get("msg", ""),
                custo=sum((i.qtd * i.custo_unit for i in ped.itens), sv.Z) + ped.custo_extra,
                arquivos={pr.id: pr.projeto for pr in (s.get(Produto, i.produto_id) for i in ped.itens if i.produto_id) if pr.projeto})


@router.post("/pedidos/{id}/atendimento")
async def atendimento(id: int, request: Request, s: Session = Depends(get_session)):
    """Muda a escolha 'atender com produto já pronto?' enquanto o pedido ainda não começou."""
    ped = s.get(Pedido, id)
    if ped.status == "aguardando" and ped.tipo == "cliente":
        ped.do_estoque = (await request.form())["do_estoque"] == "1"; s.add(ped); s.commit()
    return RedirectResponse(f"/pedidos/{id}", 303)


@router.post("/pedidos/{id}/mover")
async def mover(id: int, request: Request, s: Session = Depends(get_session)):
    ped, f = s.get(Pedido, id), await request.form()
    para = f["para"]
    consumos, insumos, faltas = [], [], []
    if ped.status == "aguardando" and para == "imprimindo":
        if ped.do_estoque and ped.tipo == "cliente":      # SIM: sai só do produto pronto
            faltas = sv.faltas_pronto(s, [(i.produto_id, i.qtd) for i in ped.itens])
        else:                                              # NÃO: imprime, gasta só filamento/insumos
            if (sv.plano_consumo(s, ped) or sv.plano_insumos(s, ped)) and not f.get("confirmado"):  # confirmar o que será gasto
                return RedirectResponse(f"/pedidos/{id}?confirmar=1", 303)
            consumos = [(int(r), D(g)) for r, g in zip(f.getlist("rolo"), f.getlist("gramas")) if r]
            insumos = [(int(i), D(q)) for i, q in zip(f.getlist("ins_id"), f.getlist("ins_q")) if i]
            faltas = sv.faltas_consumo(s, ped, consumos, insumos)
    elif para == "entregue" and ped.status == "enviado":
        faltas = sv.faltas_pronto(s, [(i.produto_id, i.qtd_pronta) for i in ped.itens if i.qtd_pronta])
    if faltas and not f.get("forcar"):
        return barreira(request, faltas, f"/pedidos/{id}/mover", f, f"/pedidos/{id}",
                        "iniciar a impressão" if para == "imprimindo" else "entregar")
    erro = sv.mover_pedido(s, ped, para, consumos, insumos)
    return RedirectResponse(f"/pedidos/{id}" + (f"?msg={erro}" if erro else ""), 303)


@router.post("/pedidos/{id}/falha")
async def falha(id: int, request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    if f.get("rolo") and D(f.get("gramas")) > 0:
        sv.registrar_falha(s, s.get(Pedido, id), int(f["rolo"]), D(f["gramas"]), f.get("obs", ""))
    return RedirectResponse(f"/pedidos/{id}?msg=Falha registrada", 303)
