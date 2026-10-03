# Bloom filters (BF.*): no false negatives, a small chance of false positives
c() { valkey-cli -h dragonfly "$@"; }

c DEL seen:emails > /dev/null
c BF.RESERVE seen:emails 0.001 1000
echo "BF.MADD ann@x bob@x -> $(c BF.MADD seen:emails ann@x bob@x | tr '\n' ' ')"
echo "BF.ADD ann@x again -> $(c BF.ADD seen:emails ann@x) (already there)"
out=$(c BF.MEXISTS seen:emails ann@x bob@x eve@x | tr '\n' ' ')
echo "BF.MEXISTS ann@x bob@x eve@x -> $out"
[ "$out" = "1 1 0 " ] || { echo "FAIL: expected 1 1 0"; exit 1; }
