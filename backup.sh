#!/bin/sh
# Serviço "backup": (1) auto-restaura se o banco subir VAZIO e houver backup com dados; (2) faz backup periódico.
export PGHOST=db PGUSER=controle PGDATABASE=controle

restaurar_se_vazio() {
  i=0
  until [ "$(psql -tAc "select to_regclass('public.usuario')" 2>/dev/null)" = "usuario" ]; do   # espera o app migrar
    i=$((i + 1)); [ $i -gt 60 ] && return; sleep 3
  done
  [ "$(psql -tAc 'select count(*) from usuario')" = "0" ] || return          # já tem dados: não mexe
  f=$(ls -t /backups/controle-*.sql.gz 2>/dev/null | head -1); [ -n "$f" ] || return
  # só restaura backup que já teve uso de verdade (tem usuário cadastrado)
  zcat "$f" | awk '/^COPY public.usuario /{d=1;next} d&&/^\\\./{exit} d{c++} END{exit !c}' || return
  echo "BANCO VAZIO e backup encontrado: restaurando $f"
  psql -q -c "drop schema public cascade; create schema public" && zcat "$f" | psql -q >/dev/null && echo "banco restaurado"
  a=$(ls -t /backups/arquivos-*.tar.gz 2>/dev/null | head -1)
  if [ -n "$a" ] && [ -z "$(ls -A /uploads)" ]; then tar xzf "$a" -C /uploads && echo "arquivos restaurados"; fi
  touch /backups/.agora
}

restaurar_se_vazio

while true; do
  if [ -f /backups/.agora ] || [ ! -f /backups/.ultimo ] || [ -n "$(find /backups/.ultimo -mmin +360)" ]; then
    rm -f /backups/.agora
    d=$(date +%F-%H%M)
    if pg_dump > /backups/controle-$d.sql && gzip -f /backups/controle-$d.sql; then touch /backups/.ultimo
    else rm -f /backups/controle-$d.sql; sleep 60; fi
    sig=$(ls -lR /uploads | md5sum)   # só arquiva se a lista de arquivos (nomes/tamanhos) mudou
    if [ "$sig" != "$(cat /backups/.uploads.sig 2>/dev/null)" ]; then
      tar czf /backups/arquivos-$d.tar.gz -C /uploads . && echo "$sig" > /backups/.uploads.sig
    fi
    find /backups -name 'controle-*.sql.gz' -mtime +30 -delete
    ls -t /backups/arquivos-*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm
  fi
  sleep 10
done
