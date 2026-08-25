#!/usr/bin/env bash
# Run the xadmin suite across the supported matrix, inside the project container.
#
# The fork claims Django 4.2 through 5.2 on Python 3.10 through 3.14. Only two
# interpreters exist in the image (Ubuntu 26.04 ships 3.10 and 3.14, with no 3.13
# candidate in apt), so the matrix is measured at the corners:
#
#     py3.10 + Django 4.2.x   <- what production runs today
#     py3.10 + Django 5.2.x   <- the target, Django axis
#     py3.14 + Django 5.2.x   <- the target, Python axis
#
# The 4.2 cell reuses the project's own pyenv. The two 5.2 cells get throwaway
# virtualenvs under /tmp/x7093.
#
# Run it from the host, from docker-project/:
#     docker compose exec -T django bash /opt/project/packages/django-xadmin/matrix.sh
#
# Add --build to (re)create the 5.2 virtualenvs; without it they are reused.
set -u

FORK=/opt/project/packages/django-xadmin
PYENV=/opt/project/pyenv/bin/python
VENVS=/tmp/x7093
# Derived from requirements.txt, never hardcoded: a literal list drifts away from what
# the package actually declares, silently, and the matrix would stop measuring the truth.
DEPS=$(grep -viE '^[[:space:]]*(#|django[><=~!])' "$FORK/requirements.txt" | tr '\n' ' ')
# Os extras de planilha nao estao no requirements.txt (sao extras_require['Excel']),
# mas a suite de export precisa deles para exercitar xlsx/xls de verdade -- sem isso
# os testes pulam e a celula da PASSED sem ter medido o que importa. xlrd e so leitura.
DEPS="$DEPS xlsxwriter xlwt xlrd"

build_cell() {   # $1 = interpreter, $2 = venv dir, $3 = django spec
	# virtualenv rather than `python -m venv`: the 3.14 in this image has no
	# ensurepip, so venv produces an environment with no pip AND still exits 0.
	"$PYENV" -m virtualenv -q -p "$1" "$2" || return 1
	# shellcheck disable=SC2086
	"$2/bin/python" -m pip install -q "$3" $DEPS || return 1
}

if [ "${1:-}" = "--build" ]; then
	echo "building the 5.2 cells..."
	rm -rf "$VENVS"; mkdir -p "$VENVS"
	build_cell /usr/bin/python3.10 "$VENVS/py310-dj52" 'Django==5.2.*' || { echo "FAILED to build the py3.10 cell"; exit 1; }
	build_cell /usr/bin/python3.14 "$VENVS/py314-dj52" 'Django==5.2.*' || { echo "FAILED to build the py3.14 cell"; exit 1; }
fi

cd "$FORK" || exit 1
find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

status=0
ran=0
CELLS=3
run_cell() {   # $1 = interpreter, $2 = extra PYTHONPATH
	local label
	# A cell that cannot run is a FAILURE, not a skip. Returning 0 here would let the
	# script print "matrix PASSED" having measured only Django 4.2 -- a false green on
	# the exact harness that certifies the 5.2 claim.
	label=$("$1" -c 'import sys, django; print("py%d.%d + Django %s" % (sys.version_info[0], sys.version_info[1], django.get_version()))' 2>/dev/null) \
		|| { printf '\n======== MISSING CELL: %s ========\n' "$1"; echo "django not importable -- run with --build"; status=1; return 0; }
	printf '\n======== %s ========\n' "$label"
	ran=$((ran + 1))
	PYTHONPATH="$FORK${2:+:$2}" "$1" runtests.py -v "${VERBOSITY:-1}" || status=1
}

# The pyenv cell needs the container's own PYTHONPATH kept, so xadmin's declared
# dependencies resolve; the throwaway venvs must NOT see it (it would let the
# pyenv's Django 4.2 win over the venv's 5.2 and silently measure the wrong thing).
run_cell "$PYENV" "${PYTHONPATH:-}"
unset PYTHONPATH
run_cell "$VENVS/py310-dj52/bin/python"
run_cell "$VENVS/py314-dj52/bin/python"

if [ "$ran" -ne "$CELLS" ]; then
	printf '\n======== matrix INCOMPLETE: %d of %d cells ran ========\n' "$ran" "$CELLS"
	exit 1
fi
printf '\n======== matrix %s (%d cells) ========\n' "$([ $status -eq 0 ] && echo PASSED || echo FAILED)" "$ran"
exit $status
