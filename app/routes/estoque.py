import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import Insumo, Material, MovEstoque, Produto, Rolo
from ..util import D, page

router = APIRouter()


def _volta(aba, msg=""):
    return RedirectResponse(f"/estoque?aba={aba}&msg={msg}", 303)


@router.get("/estoque")
def estoque(request: Request, aba: str = "filamentos", s: Session = Depends(get_session)):
    sd = sv.saldos(s, "rolo")
    rolos = s.exec(select(Rolo).where(Rolo.ativo).order_by(Rolo.id.desc())).all()
    baixo = sv.estoque_baixo(s)
    st = {a["key"]: a["status"] for a in baixo}
    mats = []
    for m in s.exec(select(Material).where(Material.ativo).order_by(Material.tipo, Material.cor)):
        tot = sum((sd.get(r.id, sv.Z) for r in rolos if r.material_id == m.id), sv.Z)
        if any(r.material_id == m.id for r in rolos):
            mats.append((m, tot, st.get(("material", m.id), "")))
    return page(request, "estoque.html", aba=aba, st=st, mats=mats, msg=request.query_params.get("msg", ""),
                rolos=[(r, sd.get(r.id, sv.Z)) for r in rolos],
                materiais=s.exec(select(Material).where(Material.ativo).order_by(Material.tipo, Material.cor)).all(),
                insumos=[(i, sv.saldo(s, "insumo", i.id)) for i in s.exec(select(Insumo).where(Insumo.ativo).order_by(Insumo.nome))],
                produtos=[(p, sv.saldo(s, "produto", p.id)) for p in s.exec(select(Produto).where(Produto.ativo).order_by(Produto.nome))],
                movs=s.exec(select(MovEstoque).order_by(MovEstoque.id.desc()).limit(200)).all(),
                nomes=_nomes(s), baixo=baixo)


def _nomes(s):
    n = {}
    for r in s.exec(select(Rolo)):
        n[("rolo", r.id)] = f"Rolo #{r.id} {r.material.nome}"
    for i in s.exec(select(Insumo)):
        n[("insumo", i.id)] = i.nome
    for p in s.exec(select(Produto)):
        n[("produto", p.id)] = p.nome
    return n


def _material(s, tipo, cor, marca, minimo=None):
    tipo, cor, marca = tipo.strip().upper(), cor.strip(), marca.strip()
    m = s.exec(select(Material).where(Material.tipo == tipo, Material.cor == cor, Material.marca == marca)).first()
    if not m:
        m = Material(tipo=tipo, cor=cor, marca=marca)
    if minimo:
        m.minimo_g = minimo
    s.add(m); s.flush()
    return m


def _compra(s, m, peso, preco, quando):
    r = Rolo(material_id=m.id, peso_inicial=peso, preco=preco, comprado_em=quando)
    s.add(r); s.flush()
    sv.mov(s, "rolo", r.id, peso, "compra", "rolo", r.id)


@router.post("/estoque/compra")
async def compra(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    m = _material(s, f["tipo"], f.get("cor", ""), f.get("marca", ""), D(f.get("minimo_g")) or None)
    for _ in range(max(1, int(D(f.get("qtd"), 1)))):
        _compra(s, m, D(f["peso"], 1000), D(f["preco"]), date.fromisoformat(f["data"]) if f.get("data") else date.today())
    s.commit()
    return _volta("filamentos", "Compra registrada")


@router.post("/estoque/rolo/{id}/ajuste")
async def rolo_ajuste(id: int, request: Request, s: Session = Depends(get_session)):
    novo = D((await request.form())["restante"])
    atual = sv.saldo(s, "rolo", id)
    if novo != atual:
        sv.mov(s, "rolo", id, novo - atual, "ajuste", "rolo", id, "pesagem")
    s.commit()
    return _volta("filamentos", "Peso ajustado")


@router.post("/estoque/rolo/{id}/arquivar")
def rolo_arquivar(id: int, s: Session = Depends(get_session)):
    r = s.get(Rolo, id); r.ativo = False; s.add(r); s.commit()
    return _volta("filamentos")


@router.post("/estoque/insumo")
async def insumo_salvar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    i = Insumo(nome=f["nome"].strip(), unidade=f.get("unidade", "un"), custo_unit=D(f.get("custo_unit")), minimo=D(f.get("minimo")))
    s.add(i); s.flush()
    if D(f.get("qtd")):
        sv.mov(s, "insumo", i.id, D(f["qtd"]), "compra", "insumo", i.id)
    s.commit()
    return _volta("insumos", "Insumo criado")


@router.post("/estoque/mov")
async def mov_manual(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    tipo, id_ = f["tipo"], int(f["item_id"])
    sv.mov(s, tipo, id_, D(f["delta"]), f.get("motivo", "ajuste"), obs=f.get("obs", ""))
    s.commit()
    return _volta("insumos" if tipo == "insumo" else "produtos", "Movimento registrado")


@router.post("/estoque/importar")
async def importar(request: Request, s: Session = Depends(get_session)):
    """CSV (;|,) colunas: tipo(filamento|insumo), material, cor, marca, peso_g, preco | nome, unidade, qtd, custo_unit."""
    f = await request.form()
    texto = (await f["arquivo"].read()).decode("utf-8-sig")
    rd = csv.DictReader(io.StringIO(texto), delimiter=";" if texto.split("\n")[0].count(";") else ",")
    n = 0
    for row in rd:
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if row.get("tipo", "").lower() == "filamento":
            m = _material(s, row.get("material", "PLA"), row.get("cor", ""), row.get("marca", ""))
            _compra(s, m, D(row.get("peso_g"), 1000), D(row.get("preco")), date.today()); n += 1
        elif row.get("tipo", "").lower() == "insumo" and row.get("nome"):
            i = Insumo(nome=row["nome"], unidade=row.get("unidade") or "un", custo_unit=D(row.get("custo_unit")))
            s.add(i); s.flush()
            if D(row.get("qtd")):
                sv.mov(s, "insumo", i.id, D(row["qtd"]), "compra", "insumo", i.id)
            n += 1
    s.commit()
    return _volta("filamentos", f"{n} itens importados")
