# Controle 3D Conex Lab

Sistema de gestão (orçamentos, pedidos/produção, estoque de filamento, produtos, projetos, vendas, financeiro, relatórios, agenda) para rodar **no computador local**. Veja `SPEC.md` para o desenho completo.

## Instalar no PC da cliente (Windows)
1. Ative a virtualização na BIOS (Intel VT-x), se ainda não estiver, e instale o **Docker Desktop** (modo WSL2). Em *Settings → General* marque **Start Docker Desktop when you sign in**.
2. Copie esta pasta para o PC, copie `.env.example` para `.env` e troque `DB_PASSWORD` e `SECRET_KEY` por textos longos aleatórios.
3. Na pasta, rode: `docker compose up -d --build` (as imagens baixam na primeira vez).
4. Abra **http://localhost:6000**, crie o usuário e a senha (primeiro acesso). Os serviços sobem sozinhos com o Windows (`restart: unless-stopped`).
5. Celular na mesma rede Wi‑Fi: `http://<IP-do-PC>:6000` (use IP fixo no roteador). Não exponha a porta 6000 na internet.

## Rotina
- **Backup**: automático a cada 24 h em `backups/` (guarda 30 dias) + botão em *Configurações*. Copie essa pasta para pendrive/nuvem de vez em quando.
- **Restaurar** (cuidado: substitui os dados atuais):
  `docker compose down` → `docker compose up -d db` →
  `docker compose exec -T db psql -U controle -c "drop database controle" -c "create database controle"` →
  `gunzip -c backups/controle-AAAA-MM-DD-HHMM.sql.gz | docker compose exec -T db psql -U controle controle` → `docker compose up -d`.
- **Atualizar o sistema**: substitua a pasta `app/`, rode `docker compose up -d --build` (as migrações do banco rodam sozinhas).

## IA local (opcional)
PC sem GPU: use modelo pequeno (padrão `qwen2.5:1.5b`, respostas em ~10–40 s).
`docker compose --profile ia up -d` e depois `docker compose exec ollama ollama pull qwen2.5:1.5b`.
O restante do sistema funciona sem isso.

## Desenvolvimento
- Testes: `docker compose exec app python -m pytest app/tests -q` (usam SQLite, não tocam no banco real).
- Mudou um modelo? `docker compose exec app alembic -c app/alembic.ini revision --autogenerate -m "descrição"` e reinicie.
