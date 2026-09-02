#!/bin/zsh
# 推送前闸门 —— 四道关不过就**不推**。（移植自 aiduMEI，v2.4.1）
# 立此脚本的原因（aiduMEI 原文照录）：我把 `git push` 无条件串在闸门后面跑，
# 闸门报了「硬敏感命中 1 次」我没读退出码，带着命中把提交推进了小仓。
# 纪律靠记性执行，早晚会失效一次 —— 焊成脚本。
#
# 用法：scripts/push_gate.sh   （通过后再手动 git push；本脚本不替你 push）
set -e
cd "$(git rev-parse --show-toplevel)"
unset ALL_PROXY all_proxy http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
PY=./.venv/bin/python
[ -x "$PY" ] || { echo "🛑 [停推] 缺 ./.venv —— 先 python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' lark-oapi"; exit 1; }
fail() { echo "🛑 [停推] $1"; exit 1; }

# ── 关一：测试 ──
$PY -m pytest tests/ -q > /tmp/g_t.log 2>&1 || fail "测试关未过：$(tail -1 /tmp/g_t.log)"

# ── 关二：静态（只拦真缺陷类 F821 未定义名 / F811 重复定义，同 aiduMEI 裁决）──
$PY -m ruff check . --select F821,F811 --output-format concise \
    --exclude .venv --exclude build --exclude '.*egg-info*' > /tmp/g_ruff.log 2>&1 \
    || fail "静态关未过（F821/F811 是运行时会炸的形态）：$(head -3 /tmp/g_ruff.log | tr '\n' ' ')"
echo "  ✅ 静态关：F821/F811 零命中"
echo "  ✅ 测试关：$(tail -1 /tmp/g_t.log)"

# ── 关三：编译 ──
$PY -m compileall -q aowen cardkit config controller feishu flush patching plugin state studio scripts tests __init__.py __main__.py setup.py > /tmp/g_c.log 2>&1 \
  || fail "编译关未过"
echo "  ✅ 编译关：0 语法错误"

# ── 关四：脱密（面① 工作区 + 面② 待推提交信息）──
WORDLIST="$HOME/.config/aidupop/scan_words.txt"
[ -f "$WORDLIST" ] || fail "脱密关未过：词表 $WORDLIST 不存在 —— 空词表 = 假绿灯，宁可不推"
files=("${(@f)$(git ls-files; git ls-files --others --exclude-standard | sort -u)}")
txt=(); for f in $files; do
  case "$f" in *.png|*.jpg|*.jpeg|*.gif|*.ico|*.webp|*.woff|*.woff2|*.ttf|*.gz|*.zip|*.pyc);;
  *) [[ -f "$f" ]] && txt+=("$f");; esac
done
AIDUPOP_SCAN_WORDLIST="$WORDLIST" \
  $PY scripts/release_scan.py "${txt[@]}" > /tmp/g_s.log 2>&1 \
  || fail "脱密关·面①未过：$(grep '总计硬敏感命中' /tmp/g_s.log)"
echo "  ✅ 脱密关面①：$(grep '总计硬敏感命中' /tmp/g_s.log)（射程 ${#txt[@]}）"

# 面②基线：在大仓时取 origin/main，在小仓时取 big_local/main（未公开提交）。
BASE_REF="origin/main"
if git rev-parse --verify -q big_local/main >/dev/null 2>&1; then
    BASE_REF="big_local/main"
fi
git log --format='%B' "$BASE_REF"..HEAD > /tmp/g_m.txt 2>/dev/null || true
if [ -s /tmp/g_m.txt ]; then
  AIDUPOP_SCAN_WORDLIST="$WORDLIST" \
    $PY scripts/release_scan.py /tmp/g_m.txt > /tmp/g_m.log 2>&1 \
    || fail "脱密关·面②（提交信息）未过"
  echo "  ✅ 脱密关面②：$(grep '总计硬敏感命中' /tmp/g_m.log)"
fi
echo "  ── 四道关全过，可以推 ──"
