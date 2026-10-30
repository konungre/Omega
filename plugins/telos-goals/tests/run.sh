#!/bin/sh
#
# Run the plugin's tests inside an Omega checkout.
#
# The tests import Omega's own modules, so they run from the root of an Omega
# checkout with this repository cloned into plugins/telos-goals, against a
# PeTTa installation:
#
#   git clone https://github.com/singnet/Omega.git
#   git clone <this repository> Omega/plugins/telos-goals
#   cd Omega && PETTA_PATH=/PeTTa sh plugins/telos-goals/tests/run.sh
#
# The Omega image ships PeTTa at /PeTTa:
#
#   docker run --rm -v "$PWD:/omega" -w /omega -e PETTA_PATH=/PeTTa \
#     singularitynet/omega:latest sh plugins/telos-goals/tests/run.sh

set -eu

if [ -z "${PETTA_PATH:-}" ] || [ ! -r "${PETTA_PATH}/run.sh" ]; then
    echo "PETTA_PATH must contain the path to the PeTTa directory"
    exit 1
fi

if [ ! -r "./src/skills.metta" ]; then
    echo "Run this from the root of an Omega checkout"
    exit 1
fi

status=0

for f in ./plugins/telos-goals/tests/tests_*.metta; do
    echo "Running $f"
    full=$(sh "${PETTA_PATH}/run.sh" "$f")
    error=$?
    output=$(echo "$full" | grep "is " | grep " should " || true)
    if [ $error -ne 0 ] || echo "$output" | grep -q "❌" || ! echo "$output" | grep -q "✅"; then
        echo "Full output:"
        echo "$full"
        echo "FAILURE in $f:"
        echo "$output"
        status=1
    else
        echo "OK: $f"
        echo "$output"
    fi
done

exit $status
