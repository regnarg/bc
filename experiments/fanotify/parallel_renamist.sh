#!/bin/bash

if [[ -z "$1" ]]; then
    self=$(readlink -f "$0")
    tmpd="$(mktemp -d)"
    cd "$tmpd"
    exec "$self" 10
fi


level="$1"
if (( level <= 0 )); then exit; fi
mkdir 0
cd 0

"$0"  $((level-1)) &
child=$!

cleanup() {
    echo "cleanup"
    kill -INT $child
    exit
}
trap cleanup INT TERM EXIT
cd ..

i=0

while true; do
    mv $i $((++i))
done


