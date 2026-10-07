"""Clientes, Configurações (parâmetros, impressoras, canais, usuário, backup) e Calculadora."""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session, or_, select

from .. import services as sv
from ..auth import confere, hash_senha
from ..calc import calcular
from ..db import get_session
from ..models import (Canal, Cliente, Impressora, Insumo, Material, Orcamento, Pedido, Produto, ProdutoInsumo, ProdutoMaterial,
                      Projeto, Usuario, Venda)
from ..util import D, page, paginate

router = APIRouter()
BACKUPS = Path("/backups")


# ---- Clientes
@router.get("/clientes")
def clientes(request: Request, q: str = "", pagina: int = 1, s: Session = Depends(get_session)):
    st = select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)
    if q:
        st = st.where(or_(Cliente.nome.ilike(f"%{q}%"), Cliente.telefone.ilike(f"%{q}%"), Cliente.email.ilike(f"%{q}%")))
    return page(request, "clientes.html", pg=paginate(s, st, pagina), q=q, c=None)


@router.get("/clientes/{id}")
def cliente_ficha(id: int, request: Request, s: Session = Depends(get_session)):
    c = s.get(Cliente, id)
    vs = s.exec(select(Venda).where(Venda.cliente_id == id, Venda.status == "ativa").order_by(Venda.data.desc())).all()
    return page(request, "cliente.html", c=c, vs=vs, total=sum((v.total for v in vs), sv.Z),
                os=s.exec(select(Orcamento).where(Orcamento.cliente_id == id).order_by(Orcamento.id.desc())).all(),
                ps=s.exec(select(Pedido).where(Pedido.cliente_id == id).order_by(Pedido.id.desc())).all())


@router.post("/clientes")
async def cliente_salvar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    c = s.get(Cliente, int(f["id"])) if f.get("id") else Cliente(nome="")
    for k in ("nome", "telefone", "email", "documento", "endereco", "observacoes"):
        setattr(c, k, f.get(k, "").strip())
    s.add(c); s.commit()
    return RedirectResponse(f"/clientes/{c.id}", 303)


@router.post("/clientes/{id}/arquivar")
def cliente_arquivar(id: int, s: Session = Depends(get_session)):
    c = s.get(Cliente, id); c.ativo = False; s.add(c); s.commit()
    return RedirectResponse("/clientes", 303)


# ---- Configurações
@router.get("/config")
def config_get(request: Request, s: Session = Depends(get_session)):
    bks = sorted([*BACKUPS.glob("controle-*.sql.gz"), *BACKUPS.glob("arquivos-*.tar.gz")], key=lambda f: f.stat().st_mtime, reverse=True)[:12] if BACKUPS.exists() else []
    return page(request, "config.html", c=sv.get_config(s), imps=s.exec(select(Impressora).order_by(Impressora.id)).all(),
                canais=s.exec(select(Canal).order_by(Canal.id)).all(), backups=[b.name for b in bks],
                msg=request.query_params.get("msg", ""))


@router.post("/config")
async def config_post(request: Request, s: Session = Depends(get_session)):
    c = sv.get_config(s)
    for k, v in (await request.form()).items():
        if k in ("cnpj", "endereco", "pix"):
            setattr(c, k, v.strip())
        elif k in ("kwh", "hora_trabalho", "falhas_pct", "margem_pct", "custos_fixos_mes", "horas_mes"):
            setattr(c, k, D(v))
    s.add(c); s.commit()
    return RedirectResponse("/config?msg=Salvo", 303)


@router.post("/impressoras")
async def impressora_salvar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    i = s.get(Impressora, int(f["id"])) if f.get("id") else Impressora(nome="")
    i.nome = f["nome"].strip()
    for k in ("watts", "valor", "vida_h", "manutencao_h"):
        setattr(i, k, D(f.get(k)))
    i.ativo = f.get("ativo", "1") == "1"
    s.add(i); s.commit()
    return RedirectResponse("/config?msg=Impressora salva", 303)


@router.post("/canais")
async def canal_salvar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    c = s.get(Canal, int(f["id"])) if f.get("id") else Canal(nome="")
    c.nome = f["nome"].strip(); c.taxa_pct = D(f.get("taxa_pct")); c.taxa_fixa = D(f.get("taxa_fixa"))
    c.ativo = f.get("ativo", "1") == "1"
    s.add(c); s.commit()
    return RedirectResponse("/config?msg=Canal salvo", 303)


@router.post("/senha")
async def trocar_senha(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    u = s.get(Usuario, request.session["uid"])
    if not confere(f["atual"], u.senha_hash) or len(f["nova"]) < 6:
        return RedirectResponse("/config?msg=Senha atual incorreta ou nova muito curta", 303)
    u.senha_hash = hash_senha(f["nova"]); s.add(u); s.commit()
    return RedirectResponse("/config?msg=Senha alterada", 303)


@router.post("/backup")
def backup_agora():
    BACKUPS.mkdir(exist_ok=True)
    (BACKUPS / ".agora").touch()
    return RedirectResponse("/config?msg=Backup solicitado (aparece na lista em ~30s)", 303)


@router.get("/backup/{nome}")
def backup_baixar(nome: str):
    if not re.fullmatch(r"(controle-[\d-]+\.sql|arquivos-[\d-]+\.tar)\.gz", nome) or not (BACKUPS / nome).exists():
        return RedirectResponse("/config", 303)
    return FileResponse(BACKUPS / nome, filename=nome)


# ---- Calculadora
def _calc_ctx(s):
    return dict(c=sv.get_config(s), imps=s.exec(select(Impressora).where(Impressora.ativo)).all(),
                canais=s.exec(select(Canal).where(Canal.ativo)).all(), materiais=s.exec(select(Material).where(Material.ativo)).all(),
                insumos=s.exec(select(Insumo).where(Insumo.ativo)).all(),
                projetos=s.exec(select(Projeto).where(Projeto.ativo).order_by(Projeto.nome)).all())


VAZIO = dict(material_id="", g="", preco="", peso_rolo="1000")


@router.get("/calculadora")
def calc_get(request: Request, projeto_id: int = 0, s: Session = Depends(get_session)):
    j = s.get(Projeto, projeto_id) if projeto_id else None
    f = {"projeto_id": str(j.id), "horas": str(round(j.tempo_min / 60, 2))} if j else {}
    fils = [dict(VAZIO, g=str(j.peso_g))] if j else [VAZIO]
    return page(request, "calculadora.html", r=None, f=f, fils=fils, ins=[], outros=[], por_canal=[], detalhe=[], ins_det=[], **_calc_ctx(s))


@router.post("/calculadora")
async def calc_post(request: Request, s: Session = Depends(get_session)):
    form = await request.form()
    ctx = _calc_ctx(s)
    res = sv.calcular_form(s, form, ctx["c"])
    por_canal = [(cn, calcular(ctx["c"], res["imp"], res["fil_total"], res["horas"], taxa_pct=cn.taxa_pct,
                               taxa_fixa=cn.taxa_fixa, **res["kw"])["preco"]) for cn in ctx["canais"]]
    return page(request, "calculadora.html", r=res["r"], f=dict(form), fils=res["fils"] or [VAZIO], ins=res["ins"],
                outros=res["outros"], detalhe=res["detalhe"], ins_det=res["ins_det"], por_canal=por_canal, **ctx)


@router.post("/calculadora/salvar")
async def calc_salvar(request: Request, s: Session = Depends(get_session)):
    form = await request.form()
    f, res = dict(form), sv.calcular_form(s, form)
    sku = f.get("sku", "").strip() or f"P{len(s.exec(select(Produto)).all()) + 1:04d}"
    if s.exec(select(Produto).where(Produto.sku == sku)).first():
        sku += "-2"
    p = Produto(nome=f["nome"], sku=sku, tempo_min=int(res["horas"] * 60), embalagem=D(f.get("embalagem")),
                acessorios=D(f.get("acessorios")) + res["out_total"], min_trabalho=int(D(f.get("min_trabalho"))),
                margem_pct=D(f.get("margem_pct")), lote_qtd=res["kw"]["lote"], preco=D(f.get("preco")),
                projeto_id=int(f["projeto_id"]) if f.get("projeto_id") else None,
                impressora_id=int(f["impressora_id"]) if f.get("impressora_id") else None)
    s.add(p); s.flush()
    for l in res["fils"]:  # só filamentos do estoque viram material do produto; os manuais ficam de fora
        if l["material_id"]:
            s.add(ProdutoMaterial(produto_id=p.id, material_id=int(l["material_id"]), gramas=D(l["g"])))
    for i in res["ins"]:
        s.add(ProdutoInsumo(produto_id=p.id, insumo_id=int(i["insumo_id"]), qtd=D(i["q"])))
    s.commit()
    return RedirectResponse(f"/produtos/{p.id}", 303)
