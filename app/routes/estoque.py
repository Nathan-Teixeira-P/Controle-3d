import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import Insumo, Material, MovEstoque, Produto, Rolo
from ..util import D, barreira, page

router = APIRouter()


def _volta(aba, msg=""):
    return RedirectResponse(f"/estoque?aba={aba}&msg={msg}", 303)


@router.get("/estoque")
def estoque(request: Request, aba: str = "filamentos", tipo: str = "", motivo: str = "", s: Session = Depends(get_session)):
    arq = dict(rolos=s.exec(select(Rolo).where(Rolo.ativo == False).order_by(Rolo.id.desc())).all(),  # noqa: E712
               insumos=s.exec(select(Insumo).where(Insumo.ativo == False).order_by(Insumo.nome)).all(),  # noqa: E712
               produtos=s.exec(select(Produto).where(Produto.ativo == False).order_by(Produto.nome)).all())  # noqa: E712
    sd = sv.saldos(s, "rolo")
    rolos = s.exec(select(Rolo).where(Rolo.ativo).order_by(Rolo.id.desc())).all()
    baixo = sv.estoque_baixo(s)
    st = {a["key"]: a["status"] for a in baixo}
    mats = []
    for m in s.exec(select(Material).where(Material.ativo).order_by(Material.tipo, Material.cor)):
        tot = sum((sd.get(r.id, sv.Z) for r in rolos if r.material_id == m.id), sv.Z)
        if any(r.material_id == m.id for r in rolos):
            mats.append((m, tot, st.get(("material", m.id), "")))
    return page(request, "estoque.html", aba=aba, arq=arq, sd_all=sd, st=st, mats=mats, msg=request.query_params.get("msg", ""),
                rolos=[(r, sd.get(r.id, sv.Z)) for r in rolos],
                materiais=s.exec(select(Material).where(Material.ativo).order_by(Material.tipo, Material.cor)).all(),
                insumos=[(i, sv.saldo(s, "insumo", i.id)) for i in s.exec(select(Insumo).where(Insumo.ativo).order_by(Insumo.nome))],
                produtos=[(p, sv.saldo(s, "produto", p.id)) for p in s.exec(select(Produto).where(Produto.ativo).order_by(Produto.nome))],
                movs=_movimentos(s, tipo, motivo), f_tipo=tipo, f_motivo=motivo,
                nomes=_nomes(s), baixo=baixo)


def _movimentos(s, tipo, motivo):
    q = select(MovEstoque).order_by(MovEstoque.id.desc()).limit(300)
    if tipo:
        q = q.where(MovEstoque.item_tipo == tipo)
    if motivo:
        q = q.where(MovEstoque.motivo == motivo)
    return s.exec(q).all()


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


def _compra(s, m, peso, preco, quando, fornecedor=""):
    r = s.exec(select(Rolo).where(Rolo.material_id == m.id, Rolo.ativo).order_by(Rolo.id.desc())).first()
    if r:  # já existe rolo desse filamento: acrescenta nele (custo/g vira a média ponderada)
        r.peso_inicial += peso; r.preco += preco
        r.fornecedor = r.fornecedor or fornecedor.strip()
    else:
        r = Rolo(material_id=m.id, peso_inicial=peso, preco=preco, comprado_em=quando, fornecedor=fornecedor.strip())
    s.add(r); s.flush()
    sv.mov(s, "rolo", r.id, peso, "compra", "rolo", r.id, valor=preco, data=quando, fornecedor=fornecedor.strip())


@router.get("/estoque/historico/{tipo}/{id}")
def historico(request: Request, tipo: str, id: int, s: Session = Depends(get_session)):
    """tipo: material (todos os rolos dele) | insumo."""
    if tipo == "material":
        item = s.get(Material, id)
        ids = [r.id for r in s.exec(select(Rolo).where(Rolo.material_id == id))]
        q = select(MovEstoque).where(MovEstoque.item_tipo == "rolo", MovEstoque.item_id.in_(ids))
        un, volta = "g", "filamentos"
    else:
        item, un, volta = s.get(Insumo, id), "", "insumos"
        q = select(MovEstoque).where(MovEstoque.item_tipo == "insumo", MovEstoque.item_id == id)
    movs = s.exec(q.order_by(MovEstoque.em.desc(), MovEstoque.id.desc())).all()
    compras = [m for m in movs if m.motivo == "compra"]
    return page(request, "estoque_historico.html", item=item, un=un or getattr(item, "unidade", ""), volta=volta, movs=movs,
                tot_qtd=sum((m.delta for m in compras), sv.Z), tot_valor=sum((m.valor or sv.Z for m in compras), sv.Z))


@router.post("/estoque/compra")
async def compra(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    m = s.get(Material, int(f["material_id"])) if f.get("material_id") else \
        _material(s, f["tipo"], f.get("cor", ""), f.get("marca", ""), D(f.get("minimo_g")) or None)
    for _ in range(max(1, int(D(f.get("qtd"), 1)))):
        _compra(s, m, D(f["peso"], 1000), D(f["preco"]), date.fromisoformat(f["data"]) if f.get("data") else date.today(),
                f.get("fornecedor", ""))
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


@router.post("/estoque/rolo/{id}/excluir")
def rolo_excluir(id: int, s: Session = Depends(get_session)):
    for m in s.exec(select(MovEstoque).where(MovEstoque.item_tipo == "rolo", MovEstoque.item_id == id)):
        s.delete(m)
    s.delete(s.get(Rolo, id)); s.commit()
    return _volta("filamentos", "Rolo excluído")


@router.post("/estoque/{tipo}/{id}/restaurar")
def restaurar(tipo: str, id: int, s: Session = Depends(get_session)):
    x = s.get({"rolo": Rolo, "insumo": Insumo, "produto": Produto}[tipo], id)
    x.ativo = True; s.add(x); s.commit()
    return _volta("arquivados", "Item restaurado")


@router.post("/estoque/material/{id}/excluir")
def material_excluir(id: int, s: Session = Depends(get_session)):
    for r in s.exec(select(Rolo).where(Rolo.material_id == id, Rolo.ativo)):
        r.ativo = False; s.add(r)
    s.commit()
    return _volta("filamentos", "Filamento excluído")


@router.post("/estoque/insumo/{id}/excluir")
def insumo_excluir(id: int, s: Session = Depends(get_session)):
    i = s.get(Insumo, id); i.ativo = False; s.add(i); s.commit()
    return _volta("insumos", "Insumo excluído")


@router.post("/estoque/insumo")
async def insumo_salvar(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    i = Insumo(nome=f["nome"].strip(), unidade=f.get("unidade", "un"), custo_unit=D(f.get("custo_unit")), minimo=D(f.get("minimo")),
               fornecedor=f.get("fornecedor", "").strip())
    s.add(i); s.flush()
    if D(f.get("qtd")):
        sv.mov(s, "insumo", i.id, D(f["qtd"]), "compra", "insumo", i.id, valor=D(f["qtd"]) * i.custo_unit, data=date.today(), fornecedor=i.fornecedor)
    s.commit()
    return _volta("insumos", "Insumo criado")


@router.post("/estoque/producao")
async def producao(request: Request, s: Session = Depends(get_session)):
    """Registra produção direta de um produto: entra no estoque pronto e abate filamento e insumos."""
    f = await request.form()
    p, n = s.get(Produto, int(f["item_id"])), D(f["qtd"])
    if n <= 0:
        return _volta("produtos", "Informe a quantidade produzida")
    faltas = sv.faltas_producao(s, p, n)
    if faltas and not f.get("forcar"):
        return barreira(request, faltas, "/estoque/producao", f, "/estoque?aba=produtos", "registrar a produção")
    resumo = sv.registrar_producao(s, p, n, "produto", p.id, "produção registrada no estoque")
    s.commit()
    return _volta("produtos", f"{n:.0f} un. de {p.nome} produzidas. Abatido: " + ("; ".join(resumo) or "nada (sem filamento/insumo no produto)"))


@router.post("/estoque/mov")
async def mov_manual(request: Request, s: Session = Depends(get_session)):
    f = await request.form()
    tipo, id_ = f["tipo"], int(f["item_id"])
    extra = {}
    if f.get("motivo") == "compra":
        extra = dict(valor=D(f.get("valor")), fornecedor=f.get("fornecedor", "").strip(),
                     data=date.fromisoformat(f["data"]) if f.get("data") else date.today())
    sv.mov(s, tipo, id_, D(f["delta"]), f.get("motivo", "ajuste"), obs=f.get("obs", ""), **extra)
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
            _compra(s, m, D(row.get("peso_g"), 1000), D(row.get("preco")), date.today(), row.get("fornecedor", "")); n += 1
        elif row.get("tipo", "").lower() == "insumo" and row.get("nome"):
            i = Insumo(nome=row["nome"], unidade=row.get("unidade") or "un", custo_unit=D(row.get("custo_unit")), fornecedor=row.get("fornecedor", ""))
            s.add(i); s.flush()
            if D(row.get("qtd")):
                sv.mov(s, "insumo", i.id, D(row["qtd"]), "compra", "insumo", i.id)
            n += 1
    s.commit()
    return _volta("filamentos", f"{n} itens importados")
