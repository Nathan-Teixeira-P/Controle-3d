import csv
import io
import re
import unicodedata
from datetime import date
from urllib.parse import quote

import openpyxl
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from .. import services as sv
from ..db import get_session
from ..models import Insumo, Material, MovEstoque, Produto, ProdutoMaterial, Rolo
from ..util import D, barreira, page

router = APIRouter()


def _volta(aba, msg=""):
    return RedirectResponse(f"/estoque?aba={aba}&msg={quote(msg)}", 303)


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


MODELO = {
    "Filamentos": ["material", "cor", "marca", "peso_g", "preco", "qtd", "minimo_g", "fornecedor"],
    "Insumos": ["nome", "unidade", "qtd", "custo_unit", "minimo", "fornecedor"],
    "Produtos": ["nome", "sku", "categoria", "preco", "qtd", "minimo", "tempo_min", "lote_qtd", "material", "cor", "marca", "peso_g"],
}
EXEMPLO = {"Filamentos": ["PLA", "Preto", "Voolt", 1000, 90, 2, 1500, "Loja X"],
           "Insumos": ["Ímã 6mm", "un", 100, 0.5, 20, ""],
           "Produtos": ["Chaveiro", "CH-01", "Chaveiros", 12, 10, 5, 30, 1, "PLA", "Preto", "Voolt", 8]}


def _chave(k):
    k = unicodedata.normalize("NFKD", str(k)).encode("ascii", "ignore").decode().strip().lower()
    return re.sub(r"\W+", "_", k).strip("_")


def _linhas(dados: bytes, nome: str):
    """Gera (dica_de_tipo, {coluna: texto}) de um .xlsx (uma aba por tipo ou coluna 'tipo') ou .csv (coluna 'tipo')."""
    if nome.lower().endswith((".xlsx", ".xlsm")):
        wb = openpyxl.load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
        folhas = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    else:
        try:
            texto = dados.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = dados.decode("latin-1")
        pri = texto.split("\n")[0]
        folhas = [("", list(csv.reader(io.StringIO(texto), delimiter=";" if pri.count(";") >= pri.count(",") and ";" in pri else ",")))]
    for titulo, linhas in folhas:
        linhas = [r for r in linhas if any(c not in (None, "") for c in r)]
        if not linhas:
            continue
        cab = [_chave(c) if c is not None else "" for c in linhas[0]]
        for r in linhas[1:]:
            yield _chave(titulo), {k: ("" if v is None else str(v).strip()) for k, v in zip(cab, r) if k}


def _tipo_linha(row, dica):
    t = _chave(row.get("tipo") or dica)
    return "filamento" if t.startswith("fil") else "insumo" if t.startswith("ins") else "produto" if t.startswith("prod") else ""


def _por_nome(s, modelo, nome):  # em Python: lower() do SQLite não trata acentos (Ímã)
    return next((x for x in s.exec(select(modelo)) if x.nome.lower() == nome.lower()), None)


def _importar_linha(s, tipo, row, hoje):
    if tipo == "filamento":
        m = _material(s, row.get("material") or "PLA", row.get("cor", ""), row.get("marca", ""), D(row.get("minimo_g")) or None)
        if D(row.get("preco")) <= 0:
            raise ValueError("filamento sem preço")
        for _ in range(max(1, int(D(row.get("qtd"), 1)))):
            _compra(s, m, D(row.get("peso_g"), 1000), D(row.get("preco")), hoje, row.get("fornecedor", ""))
    elif tipo == "insumo":
        nome = row.get("nome", "")
        if not nome:
            raise ValueError("insumo sem nome")
        i = _por_nome(s, Insumo, nome) or Insumo(nome=nome)
        i.unidade = row.get("unidade") or i.unidade
        i.custo_unit = D(row.get("custo_unit")) or i.custo_unit
        i.minimo = D(row.get("minimo")) or i.minimo
        i.fornecedor = row.get("fornecedor") or i.fornecedor
        i.ativo = True
        s.add(i); s.flush()
        if D(row.get("qtd")):
            sv.mov(s, "insumo", i.id, D(row["qtd"]), "compra", "insumo", i.id, "importação")
    else:
        nome, sku = row.get("nome", ""), row.get("sku", "")
        if not nome:
            raise ValueError("produto sem nome")
        p = (sku and s.exec(select(Produto).where(Produto.sku == sku)).first()) or _por_nome(s, Produto, nome)
        novo = p is None
        if novo:
            p = Produto(nome=nome, sku=sku or f"P{len(s.exec(select(Produto)).all()) + 1:04d}")
        p.categoria = row.get("categoria") or p.categoria
        p.minimo = D(row.get("minimo")) or p.minimo
        p.tempo_min = int(D(row.get("tempo_min"))) or p.tempo_min
        p.lote_qtd = max(int(D(row.get("lote_qtd"), 1)), 1) if row.get("lote_qtd") else p.lote_qtd
        if D(row.get("preco")) > 0:
            p.preco, p.preco_manual = D(row["preco"]), True
        p.ativo = True
        if novo and row.get("material") and D(row.get("peso_g")) > 0:  # receita de filamento (sem comprar nada)
            m = _material(s, row["material"], row.get("cor", ""), row.get("marca", ""))
            p.materiais = [ProdutoMaterial(material_id=m.id, gramas=D(row["peso_g"]))]
        s.add(p); s.flush()
        if novo and not p.preco_manual:
            sv.atualizar_precos(s, [p])
        if D(row.get("qtd")):
            sv.mov(s, "produto", p.id, D(row["qtd"]), "ajuste", "produto", p.id, "importação")


@router.get("/estoque/modelo.xlsx")
def modelo():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for aba, cols in MODELO.items():
        ws = wb.create_sheet(aba)
        ws.append(cols); ws.append(EXEMPLO[aba])
    b = io.BytesIO(); wb.save(b)
    return Response(b.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="modelo-estoque.xlsx"'})


@router.post("/estoque/importar")
async def importar(request: Request, s: Session = Depends(get_session)):
    """Planilha .xlsx (abas Filamentos/Insumos/Produtos) ou .csv (coluna 'tipo'). Cada linha é importada isolada: erro numa não barra as outras."""
    arq = (await request.form())["arquivo"]
    n, erros = 0, []
    try:
        for i, (dica, row) in enumerate(_linhas(await arq.read(), arq.filename or ""), 2):
            tipo = _tipo_linha(row, dica)
            try:
                if not tipo:
                    raise ValueError("tipo não reconhecido")
                with s.begin_nested():
                    _importar_linha(s, tipo, row, date.today())
                n += 1
            except Exception as e:
                erros.append(f"{dica or 'linha'} {i}: {e}")
    except Exception:
        s.rollback()
        return _volta("filamentos", "Não consegui ler o arquivo. Use o modelo (.xlsx) ou um .csv.")
    s.commit()
    msg = f"{n} itens importados" + (f". {len(erros)} com erro: " + "; ".join(erros[:3]) if erros else "")
    return _volta("filamentos", msg)
