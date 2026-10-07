from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from pydantic import NaiveDatetime
from sqlmodel import Field, Relationship, SQLModel


def num(d="0", p=2):
    return Field(default=Decimal(d), max_digits=14, decimal_places=p)


def agora():
    return datetime.now().replace(microsecond=0)


# ---------- Sistema / cadastros
class Usuario(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str = Field(unique=True, index=True)
    senha_hash: str


class Config(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)  # linha única
    kwh: Decimal = num("0.95")
    hora_trabalho: Decimal = num("20")
    falhas_pct: Decimal = num("8")
    margem_pct: Decimal = num("40")
    custos_fixos_mes: Decimal = num("0")  # aluguel, internet, softwares... rateados por hora de impressão
    horas_mes: Decimal = num("160")  # horas de impressão produtivas por mês
    cnpj: str = ""
    endereco: str = ""
    pix: str = ""


class Impressora(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    watts: Decimal = num("150")
    valor: Decimal = num("3000")
    vida_h: Decimal = num("4000")
    horas_acumuladas: Decimal = num("0")
    manutencao_h: Decimal = num("0")  # R$/h
    ativo: bool = True

    @property
    def custo_hora(self):
        return (self.valor / self.vida_h if self.vida_h else Decimal(0)) + self.manutencao_h


class Canal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    taxa_pct: Decimal = num("0")
    taxa_fixa: Decimal = num("0")
    ativo: bool = True


class Cliente(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    telefone: str = ""
    email: str = ""
    documento: str = ""
    endereco: str = ""
    observacoes: str = ""
    ativo: bool = True


# ---------- Estoque
class Material(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tipo: str  # PLA, PETG...
    cor: str = ""
    marca: str = ""
    minimo_g: Decimal = num("0")
    ativo: bool = True

    @property
    def nome(self):
        return " ".join(x for x in (self.tipo, self.cor, self.marca) if x)


class Rolo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="material.id")
    peso_inicial: Decimal = num("1000")
    preco: Decimal = num("100")
    fornecedor: str = ""
    comprado_em: date = Field(default_factory=date.today)
    ativo: bool = True
    material: Optional[Material] = Relationship()

    @property
    def custo_g(self):
        return self.preco / self.peso_inicial if self.peso_inicial else Decimal(0)


class Insumo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    unidade: str = "un"
    custo_unit: Decimal = num("0", 4)
    minimo: Decimal = num("0")
    fornecedor: str = ""
    ativo: bool = True


class MovEstoque(SQLModel, table=True):
    """Livro-razão: saldo = soma dos deltas. item_tipo: rolo | insumo | produto."""
    id: Optional[int] = Field(default=None, primary_key=True)
    item_tipo: str = Field(index=True)
    item_id: int = Field(index=True)
    delta: Decimal = num("0")
    motivo: str = ""  # compra producao falha venda ajuste
    ref_tipo: str = ""
    ref_id: int = 0
    obs: str = ""
    em: NaiveDatetime = Field(default_factory=agora)


# ---------- Projetos (arquivos de modelo/fatiamento guardados no sistema)
class Projeto(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    descricao: str = ""
    categoria: str = ""
    arquivo: str = ""  # /uploads/<aleatório>.ext
    arquivo_nome: str = ""  # nome original (para download)
    tamanho: int = 0
    foto: str = ""
    peso_g: Decimal = num("0")  # do fatiador (informado ou lido do .3mf)
    tempo_min: int = 0
    criado_em: NaiveDatetime = Field(default_factory=agora)
    ativo: bool = True


# ---------- Produtos
class Produto(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    sku: str = Field(unique=True, index=True)
    categoria: str = ""
    foto: str = ""
    modelo: str = ""  # link/caminho livre (o arquivo de verdade vem de Projeto)
    projeto_id: Optional[int] = Field(default=None, foreign_key="projeto.id")
    tempo_min: int = 0
    impressora_id: Optional[int] = Field(default=None, foreign_key="impressora.id")
    preco: Decimal = num("0")
    preco_manual: bool = False  # False: o preço acompanha o sugerido (custo + margem) enquanto se edita
    margem_pct: Decimal = num("40")
    lote_qtd: int = 1  # quantas unidades saem de UMA produção; tempo, gramas, insumos e custos do produto são do lote
    fornecedor: str = ""
    minimo: Decimal = num("0")  # estoque pronto mínimo
    embalagem: Decimal = num("0")
    acessorios: Decimal = num("0")
    min_trabalho: int = 0
    ativo: bool = True
    projeto: Optional[Projeto] = Relationship()
    materiais: list["ProdutoMaterial"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    insumos: list["ProdutoInsumo"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})


class ProdutoMaterial(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    produto_id: int = Field(foreign_key="produto.id")
    material_id: int = Field(foreign_key="material.id")
    gramas: Decimal = num("0")
    material: Optional[Material] = Relationship()


class ProdutoInsumo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    produto_id: int = Field(foreign_key="produto.id")
    insumo_id: int = Field(foreign_key="insumo.id")
    qtd: Decimal = num("1")
    insumo: Optional[Insumo] = Relationship()


# ---------- Orçamentos
class Orcamento(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)  # id = nº do orçamento
    cliente_id: int = Field(foreign_key="cliente.id")
    canal_id: Optional[int] = Field(default=None, foreign_key="canal.id")
    data: date = Field(default_factory=date.today)
    validade_dias: int = 7
    desconto: Decimal = num("0")
    frete: Decimal = num("0")
    prazo: str = "5 dias úteis após a confirmação"
    pagamento: str = "50% de entrada e 50% na entrega (PIX)"
    observacoes: str = ""
    status: str = "aberto"  # aberto | aprovado | recusado
    cliente: Optional[Cliente] = Relationship()
    canal: Optional[Canal] = Relationship()
    itens: list["Item"] = Relationship(back_populates="orcamento", sa_relationship_kwargs={"cascade": "all, delete-orphan"})

    @property
    def subtotal(self):
        return sum((i.total for i in self.itens), Decimal(0))

    @property
    def total(self):
        return self.subtotal - self.desconto + self.frete

    @property
    def valido_ate(self):
        return self.data + timedelta(days=self.validade_dias)

    @property
    def status_exib(self):
        return "expirado" if self.status == "aberto" and self.valido_ate < date.today() else self.status


class Item(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    orcamento_id: int = Field(foreign_key="orcamento.id")
    produto_id: Optional[int] = Field(default=None, foreign_key="produto.id")
    descricao: str
    personalizacao: str = ""
    qtd: int = 1
    preco_unit: Decimal = num("0")
    custo_unit: Decimal = num("0")  # só para item avulso; com produto o custo é calculado
    orcamento: Optional[Orcamento] = Relationship(back_populates="itens")

    @property
    def total(self):
        return self.qtd * self.preco_unit


# ---------- Pedidos / vendas
class Pedido(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    orcamento_id: Optional[int] = Field(default=None, foreign_key="orcamento.id")
    cliente_id: Optional[int] = Field(default=None, foreign_key="cliente.id")
    canal_id: Optional[int] = Field(default=None, foreign_key="canal.id")
    tipo: str = "cliente"  # cliente | estoque (produzir para repor produto pronto)
    status: str = Field(default="aguardando", index=True)
    prazo: Optional[date] = None
    observacoes: str = ""
    desconto: Decimal = num("0")
    frete: Decimal = num("0")
    custo_extra: Decimal = num("0")  # falhas
    do_estoque: bool = False  # atendido com produto já pronto em estoque
    criado_em: NaiveDatetime = Field(default_factory=agora)
    cliente: Optional[Cliente] = Relationship()
    canal: Optional[Canal] = Relationship()
    itens: list["PedidoItem"] = Relationship(back_populates="pedido", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    historico: list["PedidoHistorico"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})

    @property
    def total(self):
        return sum((i.total for i in self.itens), Decimal(0)) - self.desconto + self.frete

    @property
    def atrasado(self):
        return bool(self.prazo and self.prazo < date.today() and self.status not in ("entregue", "cancelado")
                    and not (self.tipo == "estoque" and self.status == "pronto"))


class PedidoItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    pedido_id: int = Field(foreign_key="pedido.id")
    produto_id: Optional[int] = Field(default=None, foreign_key="produto.id")
    descricao: str
    personalizacao: str = ""
    qtd: int = 1
    preco_unit: Decimal = num("0")
    custo_unit: Decimal = num("0")  # congelado na criação
    qtd_pronta: int = 0  # unidades atendidas com produto JÁ PRONTO em estoque (não imprime; a entrega baixa o produto pronto)
    pedido: Optional[Pedido] = Relationship(back_populates="itens")

    @property
    def total(self):
        return self.qtd * self.preco_unit


class PedidoHistorico(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    pedido_id: int = Field(foreign_key="pedido.id")
    de: str = ""
    para: str = ""
    em: NaiveDatetime = Field(default_factory=agora)


class Venda(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    pedido_id: Optional[int] = Field(default=None, foreign_key="pedido.id")
    cliente_id: Optional[int] = Field(default=None, foreign_key="cliente.id")
    canal_id: Optional[int] = Field(default=None, foreign_key="canal.id")
    forma_pagamento: str = "PIX"
    data: date = Field(default_factory=date.today)
    total: Decimal = num("0")
    custo: Decimal = num("0")
    taxa: Decimal = num("0")
    lucro: Decimal = num("0")
    status: str = "ativa"  # ativa | estornada
    cliente: Optional[Cliente] = Relationship()
    canal: Optional[Canal] = Relationship()
    itens: list["VendaItem"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})


class VendaItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    venda_id: int = Field(foreign_key="venda.id")
    produto_id: Optional[int] = Field(default=None, foreign_key="produto.id")
    descricao: str
    qtd: int = 1
    preco_unit: Decimal = num("0")
    custo_unit: Decimal = num("0")


# ---------- Financeiro / agenda
class Lancamento(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tipo: str  # receita | despesa
    categoria: str = ""
    natureza: str = "variavel"  # fixa | variavel
    descricao: str = ""
    valor: Decimal = num("0")
    vencimento: date = Field(default_factory=date.today)
    pago_em: Optional[date] = None
    venda_id: Optional[int] = Field(default=None, foreign_key="venda.id")
    recorrencia: str = ""  # "" | mensal
    gerou_proximo: bool = False
    cancelado: bool = False


class Tarefa(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    titulo: str
    data: date = Field(default_factory=date.today)
    hora: str = ""
    feita: bool = False
    recorrencia: str = ""  # "" | diaria | semanal
