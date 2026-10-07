from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, or_, select

from .. import services as sv
from ..db import get_session
from ..models import Canal, Impressora, Insumo, Material, Produto, ProdutoInsumo, ProdutoMaterial, Projeto
from ..util import D, page, paginate, salvar_upload

router = APIRouter()


@router.get("/produtos")
def produtos(request: Request, q: str = "", categoria: str = "", pagina: int = 1, s: Session = Depends(get_session)):
    st = select(Produto).where(Produto.ativo).order_by(Produto.nome)
    if q:
        st = st.where(or_(Produto.nome.ilike(f"%{q}%"), Produto.sku.ilike(f"%{q}%")))
    if categoria:
        st = st.where(Produto.categoria == categoria)
    pg = paginate(s, st, pagina)
    cfg, sd, sp = sv.get_config(s), sv.saldos(s, "rolo"), sv.saldos(s, "produto")
    st = {a["key"]: a["status"] for a in sv.estoque_baixo(s)}
    linhas = []
    for p in pg.items:
        c = sv.custo_produto(s, p, cfg, sd=sd)["custo"]
        linhas.append(dict(p=p, custo=c, margem=sv.margem_real(p, c), estoque=sp.get(p.id, sv.Z), st=st.get(("produto", p.id), "")))
    cats = s.exec(select(Produto.categoria).distinct()).all()
    return page(request, "produtos.html", pg=pg, linhas=linhas, q=q, categoria=categoria, cats=[c for c in cats if c])


def _form_ctx(s):
    return dict(imps=s.exec(select(Impressora).where(Impressora.ativo)).all(),
                materiais=s.exec(select(Material).where(Material.ativo)).all(),
                insumos=s.exec(select(Insumo).where(Insumo.ativo)).all(),
                projetos=s.exec(select(Projeto).where(Projeto.ativo).order_by(Projeto.nome)).all(),
                cats=[c for c in s.exec(select(Produto.categoria).distinct()).all() if c])


@router.get("/produtos/novo")
def produto_novo(request: Request, projeto_id: int = 0, s: Session = Depends(get_session)):
    pre = s.get(Projeto, projeto_id) if projeto_id else None  # pré-preenche com o projeto escolhido
    return page(request, "produto.html", p=None, pre=pre, calc=None, por_canal=[], **_form_ctx(s))


@router.get("/produtos/{id}")
def produto_ver(id: int, request: Request, s: Session = Depends(get_session)):
    p = s.get(Produto, id)
    cfg = sv.get_config(s)
    por_canal = [(c, sv.custo_produto(s, p, cfg, c)["preco"]) for c in s.exec(select(Canal).where(Canal.ativo))]
    return page(request, "produto.html", p=p, pre=None, calc=sv.custo_produto(s, p, cfg), por_canal=por_canal,
                estoque=sv.saldo(s, "produto", id), **_form_ctx(s))


@router.post("/produtos")
async def produto_salvar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    p = s.get(Produto, int(f["id"])) if f.get("id") else Produto(nome="", sku="")
    p.nome, p.categoria = f["nome"].strip(), f.get("categoria", "").strip()
    sku = f.get("sku", "").strip()
    if not sku:
        sku = f"P{(s.exec(select(Produto)).all().__len__() + 1):04d}"
    clash = s.exec(select(Produto).where(Produto.sku == sku)).first()
    p.sku = sku if not clash or clash.id == p.id else sku + "-2"
    p.tempo_min = int(D(f.get("tempo_min")))
    p.impressora_id = int(f["impressora_id"]) if f.get("impressora_id") else None
    for k in ("preco", "margem_pct", "minimo", "embalagem", "acessorios"):
        setattr(p, k, D(f.get(k)))
    p.min_trabalho = int(D(f.get("min_trabalho")))
    p.projeto_id = int(f["projeto_id"]) if f.get("projeto_id") else None
    p.modelo = f.get("modelo_link", "").strip()
    if foto := await salvar_upload(f.get("foto"), foto=True):
        p.foto = foto
    p.materiais = [ProdutoMaterial(material_id=int(m), gramas=D(g)) for m, g in zip(f.getlist("mat_id"), f.getlist("mat_g")) if m and D(g) > 0]
    p.insumos = [ProdutoInsumo(insumo_id=int(i), qtd=D(q, 1)) for i, q in zip(f.getlist("ins_id"), f.getlist("ins_q")) if i]
    s.add(p); s.commit()
    return RedirectResponse(f"/produtos/{p.id}", 303)


@router.post("/produtos/{id}/arquivar")
def produto_arquivar(id: int, s: Session = Depends(get_session)):
    p = s.get(Produto, id); p.ativo = False; s.add(p); s.commit()
    return RedirectResponse("/produtos", 303)
