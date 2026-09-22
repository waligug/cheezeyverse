"""The cap board's data: the game's own report, and the one number it does not print.

WHERE THE NUMBERS COME FROM. `capreport.htm` gives the cap and the two exceptions, but it never
prints the LUXURY TAX - and the tax is half the point of the chart, because it is the line teams
are actually managing against. So the tax is read out of `league.dat`.

NOT AT A FIXED OFFSET. The finance block moves as the file grows: it sat at 78632 in the live pro
save and nowhere near it in a clone one season on. Instead the CAP - whose value the report has
just told us - is found in the binary and CONFIRMED by its neighbours, the two exceptions at +28
and +32, and the tax is the int32 immediately before it. Two independent sources have to agree
before a number is believed. When they do not, the tax comes back None and the chart simply draws
no tax line, which is the only safe failure: a wrong line on a money chart is worse than no line.

    python tests/test_cap_json.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner.publish import publish as P  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


REPORT = """<html><body><table>
<tr><td>#</td><td>Team</td><td>Salary</td><td>Cap Room</td><td>Budget Room</td>
    <td>Mid Exception</td><td>Low Exception</td></tr>
<tr><td>1</td><td>Rams</td><td>$1,198,675</td><td>$61,868,189</td><td>-</td>
    <td>($5,432,678)</td><td>($2,368,647)</td></tr>
<tr><td>2</td><td>Cats</td><td>$3,198,675</td><td>$59,868,189</td><td>-</td>
    <td>($5,432,678)</td><td>($2,368,647)</td></tr>
</table></body></html>"""


def block(tax, cap, mle, lle, pad=200):
    """A slab of file with one finance block in it, at an offset nobody is allowed to assume."""
    data = bytearray(b"\x00" * pad)
    data += struct.pack("<2i", tax, cap)
    data += b"\x00" * 24
    data += struct.pack("<2i", mle, lle)
    data += b"\x00" * pad
    return bytes(data)


def run():
    print("money cells, the way the game's report writes them")
    check("a plain figure", P._money("$1,198,675"), 1198675)
    # AN EXCEPTION IS PRINTED PARENTHESISED, which is this report's way of saying "room you have
    # not used". Reading it as a positive number would put the mid-level exception above the cap.
    check("a parenthesised figure is negative", P._money("($5,432,678)"), -5432678)
    check("a dash is not money", P._money("-"), None)
    check("nor is a blank", P._money(""), None)
    check("nor is a word", P._money("Rams"), None)
    check("nor is None", P._money(None), None)

    print("the cap is the SUM of salary and cap room, which the report never prints directly")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "capreport.htm").write_text(REPORT, encoding="latin-1")
        cap, mle, lle = P._cap_report(tmp)
        check("cap", cap, 1198675 + 61868189)
        # Both rows must give the same cap; that is what makes the sum trustworthy.
        check("and the second row agrees", 3198675 + 59868189, cap)
        check("mid-level exception, as a magnitude", mle, 5432678)
        check("low-level exception", lle, 2368647)

    print("the luxury tax is found by its neighbours, never by a remembered offset")
    cap, mle, lle, tax = 63_066_864, 5_432_678, 2_368_647, 77_000_000
    check("found in a slab where it sits at 200", P._luxury_tax(block(tax, cap, mle, lle), cap, mle, lle), tax)
    check("and at a completely different offset",
          P._luxury_tax(block(tax, cap, mle, lle, pad=4096), cap, mle, lle), tax)

    print("it refuses rather than guessing")
    # The cap value appears, but the exceptions beside it do not match - so this is some other
    # number that happens to equal the cap, not the finance block.
    check("the cap value alone is not the block",
          P._luxury_tax(block(tax, cap, 111, 222), cap, mle, lle), None)
    check("no cap in the file at all",
          P._luxury_tax(b"\x00" * 500, cap, mle, lle), None)
    # TWO CANDIDATES IS NOT AN ANSWER. Picking either would be a coin flip printed as a fact.
    twice = block(tax, cap, mle, lle) + block(999, cap, mle, lle)
    check("two matching blocks is ambiguous", P._luxury_tax(twice, cap, mle, lle), None)

    print("a tax of zero means the tax is OFF, and must not draw a line")
    # This is the state every league except pro is in, and the state pro itself was in this
    # morning. Zero is not a level, it is an absence.
    check("zero comes back as nothing",
          P._luxury_tax(block(0, cap, mle, lle), cap, mle, lle), None)
    check("and so does a negative", P._luxury_tax(block(-5, cap, mle, lle), cap, mle, lle), None)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  cap json: the report's own figures are read as it writes them, the luxury tax is "
          "located by agreement between two sources rather than by a fixed offset, and anything "
          "ambiguous draws no line at all")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
