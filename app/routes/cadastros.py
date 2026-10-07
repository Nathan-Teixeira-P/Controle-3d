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
from ..models import Canal, Cliente, Impressora, Material, Orcamento, Pedido, Produto, ProdutoMaterial, Projeto, Usuario, Venda
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
    bks = sorted(BACKUPS.glob("controle-*.sql.gz"), reverse=True)[:10] if BACKUPS.exists() else []
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
    if not re.fullmatch(r"controle-[\d-]+\.sql\.gz", nome) or not (BACKUPS / nome).exists():
        return RedirectResponse("/config", 303)
    return FileResponse(BACKUPS / nome, filename=nome)


# ---- Calculadora
def _calc_ctx(s):
    return dict(c=sv.get_config(s), imps=s.exec(select(Impressora).where(Impressora.ativo)).all(),
                canais=s.exec(select(Canal).where(Canal.ativo)).all(), materiais=s.exec(select(Material).where(Material.ativo)).all(),
                projetos=s.exec(select(Projeto).where(Projeto.ativo).order_by(Projeto.nome)).all())


@router.get("/calculadora")
def calc_get(request: Request, projeto_id: int = 0, s: Session = Depends(get_session)):
    j = s.get(Projeto, projeto_id) if projeto_id else None
    f = {"projeto_id": str(j.id), "horas": str(round(j.tempo_min / 60, 2))} if j else {}
    fils = [dict(material_id="", g=str(j.peso_g), preco="", peso_rolo="1000")] if j else [dict(material_id="", g="", preco="", peso_rolo="1000")]
    return page(request, "calculadora.html", r=None, f=f, fils=fils, outros=[], por_canal=[], **_calc_ctx(s))


def _linhas(form):
    fils = [dict(material_id=m, g=g, preco=p, peso_rolo=pr) for m, g, p, pr in
            zip(form.getlist("mat_id"), form.getlist("mat_g"), form.getlist("mat_preco"), form.getlist("mat_pesorolo")) if D(g) > 0]
    outros = [dict(desc=d, valor=v) for d, v in zip(form.getlist("out_desc"), form.getlist("out_valor")) if D(v) != 0]
    return fils, outros


@router.post("/calculadora")
async def calc_post(request: Request, s: Session = Depends(get_session)):
    form = await request.form()
    f, ctx = dict(form), _calc_ctx(s)
    fils, outros = _linhas(form)
    imp = s.get(Impressora, int(f["impressora_id"])) if f.get("impressora_id") else None
    detalhe, fil_total = [], sv.Z
    for l in fils:
        if l["material_id"]:
            m = s.get(Material, int(l["material_id"]))
            cg, nome = sv.custo_g_material(s, m.id), m.nome
        else:
            cg, nome = D(l["preco"]) / (D(l["peso_rolo"], 1000) or 1000), "Manual"
        custo = D(l["g"]) * cg
        fil_total += custo
        detalhe.append(dict(nome=nome, g=D(l["g"]), cg=cg, custo=custo))
    out_total = sum((D(o["valor"]) for o in outros), sv.Z)
    canal = s.get(Canal, int(f["canal_id"])) if f.get("canal_id") else None
    kw = dict(embalagem=D(f.get("embalagem")), acessorios=D(f.get("acessorios")) + out_total,
              min_trabalho=D(f.get("min_trabalho")), margem_pct=D(f.get("margem_pct"), ctx["c"].margem_pct))
    r = calcular(ctx["c"], imp, fil_total, D(f["horas"]), taxa_pct=canal.taxa_pct if canal else 0,
                 taxa_fixa=canal.taxa_fixa if canal else 0, **kw)
    por_canal = [(cn, calcular(ctx["c"], imp, fil_total, D(f["horas"]), taxa_pct=cn.taxa_pct, taxa_fixa=cn.taxa_fixa, **kw)["preco"])
                 for cn in ctx["canais"]]
    return page(request, "calculadora.html", r=r, f=f, fils=fils or [dict(material_id="", g="", preco="", peso_rolo="1000")],
                outros=outros, detalhe=detalhe, out_total=out_total, por_canal=por_canal, **ctx)


@router.post("/calculadora/salvar")
async def calc_salvar(request: Request, s: Session = Depends(get_session)):
    form = await request.form()
    f, (fils, outros) = dict(form), _linhas(form)
    n = s.exec(select(Produto)).all()
    sku = f.get("sku", "").strip() or f"P{len(n) + 1:04d}"
    if s.exec(select(Produto).where(Produto.sku == sku)).first():
        sku += "-2"
    p = Produto(nome=f["nome"], sku=sku, tempo_min=int(D(f["horas"]) * 60), embalagem=D(f.get("embalagem")),
                acessorios=D(f.get("acessorios")) + sum((D(o["valor"]) for o in outros), sv.Z),
                min_trabalho=int(D(f.get("min_trabalho"))), margem_pct=D(f.get("margem_pct")),
                projeto_id=int(f["projeto_id"]) if f.get("projeto_id") else None,
                impressora_id=int(f["impressora_id"]) if f.get("impressora_id") else None, preco=D(f.get("preco")))
    s.add(p); s.flush()
    for l in fils:  # só filamentos do estoque viram material do produto; os manuais ficam de fora
        if l["material_id"]:
            s.add(ProdutoMaterial(produto_id=p.id, material_id=int(l["material_id"]), gramas=D(l["g"])))
    s.commit()
    return RedirectResponse(f"/produtos/{p.id}", 303)
