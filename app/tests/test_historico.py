def test_historico_compras(c):
    c.post("/estoque/compra", data={"tipo": "pla", "cor": "Preto", "marca": "X", "peso": "1000", "preco": "100", "qtd": "1", "data": "2026-01-10", "fornecedor": "Loja A"})
    c.post("/estoque/compra", data={"tipo": "pla", "cor": "Preto", "marca": "X", "peso": "1000", "preco": "80", "qtd": "1", "data": "2026-02-10"})
    r = c.get("/estoque/historico/material/1")
    assert r.status_code == 200 and "10/01/2026" in r.text and "10/02/2026" in r.text and "Loja A" in r.text and "R$" in r.text
    c.post("/estoque/insumo", data={"nome": "Parafuso", "qtd": "10", "custo_unit": "2"})
    c.post("/estoque/mov", data={"tipo": "insumo", "item_id": "1", "delta": "5", "motivo": "compra", "valor": "9", "data": "2026-03-01"})
    assert "01/03/2026" in c.get("/estoque/historico/insumo/1").text


def test_compra_soma_no_rolo_existente(c):
    for _ in range(2):
        c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1"})
    from sqlmodel import Session, select
    from app.db import engine
    from app.models import Rolo
    with Session(engine) as s:
        (r,) = s.exec(select(Rolo)).all()
        assert r.peso_inicial == 2000 and r.preco == 200


def test_compra_por_material_id(c):
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1"})
    assert c.post("/estoque/compra", data={"material_id": "1", "peso": "1000", "preco": "90", "qtd": "2"}).status_code == 303
    assert "2900" not in c.get("/estoque").text and "Escolha" in c.get("/estoque").text


def test_arquivados_e_restaurar(c):
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1"})
    c.post("/estoque/rolo/1/arquivar")
    assert "Rolo #1" in c.get("/estoque?aba=arquivados").text
    c.post("/estoque/rolo/1/restaurar")
    assert "Nada arquivado" in c.get("/estoque?aba=arquivados").text
