#!/usr/bin/env bash
#
# Публикует ветку в GitHub — публичное зеркало. Источник истины остаётся
# в GitLab.
#
#   tools/publish_github.sh [исходная-ветка]
#
# Здесь нет переписывания истории: в этом репозитории нет внутренних записей,
# которые нужно вырезать перед публикацией. Всё, что лежит в main, публично.
# Если однажды появится каталог, который публиковать нельзя, — фильтровать
# придётся ВСЮ историю (git-filter-repo), а не удалять его с вершины: удаление
# с вершины даёт видимость, а не результат, содержимое остаётся в предыдущих
# коммитах.
#
# Один и тот же скрипт вызывается вручную и из CI, чтобы правила публикации
# не разъезжались.

set -euo pipefail

REMOTE="${PUBLISH_REMOTE:-github}"
BRANCH="${PUBLISH_BRANCH:-main}"
SOURCE="${1:-$BRANCH}"

if [ -n "${CI:-}" ]; then
  # В CI checkout отсоединён от веток, и ветки с нужным именем локально нет.
  SOURCE_SHA="$(git rev-parse HEAD)"
  echo "режим CI: публикую текущий checkout ($(git rev-parse --short HEAD))"
else
  SOURCE_SHA="$(git rev-parse "$SOURCE")"
  echo "публикую '$SOURCE' ($(git rev-parse --short "$SOURCE"))"
fi

# Теги с зеркала не тянем никогда. Если публикация когда-нибудь начнёт
# переписывать историю, теги там будут указывать на другие коммиты, и
# `git fetch github --tags` испортит локальные.
git config "remote.${REMOTE}.tagOpt" --no-tags

echo "ветка -> $REMOTE/$BRANCH"
git push "$REMOTE" "$SOURCE_SHA:refs/heads/$BRANCH"

# Тег публикуется только тот, на котором стоит текущий прогон. Пушить все
# теги подряд незачем: старые уже на месте, а --force по всему набору однажды
# сдвинет то, что двигать не хотели.
if [ -n "${CI_COMMIT_TAG:-}" ]; then
  echo "тег -> $REMOTE/${CI_COMMIT_TAG}"
  git push "$REMOTE" "refs/tags/${CI_COMMIT_TAG}"
else
  echo "тега в этом прогоне нет"
fi
