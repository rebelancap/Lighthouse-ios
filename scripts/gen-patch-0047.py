#!/usr/bin/env python3
"""Overlay patch 0047: unconditional ImGui Begin/BeginChild terminators.

NUMBERING: 0037+ are Lighthouse-only.

FOUND BY TESTING, not by reading. Opening the **Rando** tab pops Dear ImGui's
assertion dialog over the game and leaves the menu unusable:

    In window 'Main Menu/.../General Settings_...': Missing EndTabBar()
    In window 'Main Menu/.../General Settings_...': Missing PopID()
    In window 'Main Menu/Menu Block_...': Must call EndChild() and not End()!
    In window 'Main - Deck': Missing End()

THE RULE UPSTREAM IS BREAKING. `ImGui::Begin()` and `ImGui::BeginChild()` must
be paired with `End()` / `EndChild()` **unconditionally** — the return value
says whether the contents are VISIBLE, not whether the window was pushed. The
old "skip End() when Begin() returns false" idiom was removed for child windows
in ImGui 1.90, and the version LUS ships treats it as an error. Upstream writes
the terminator inside the `if` in 17 places:

    if (ImGui::BeginChild("SeedData", ...)) {
        ...
        ImGui::EndChild();     // <-- skipped whenever the child is culled
    }

When the window or child is culled — collapsed, clipped, scrolled out of view,
or simply off-screen on a phone-sized viewport — the terminator never runs, the
window stack unwinds wrong, and ImGui reports the damage against whichever
window happens to be open when it notices. That is why the errors name 'General
Settings' and 'Main - Deck' rather than the code at fault: the assertion
surfaces far from its cause, which is what made a first pass at this patch fix
only three of the sites and change nothing the user could see.

WHY IT BITES iOS HARDER THAN DESKTOP. Every one of these children is sized from
`GetContentRegionAvail()`, so on a short viewport they clip, `BeginChild()`
returns false, and the terminator is skipped. On a desktop-sized window most of
them stay visible and the bug hides.

SCOPE — all 17 sites, not just Rando's:
  * `Rando/CheckTracker/CheckTracker.cpp`   x4  (one is a `Begin()`/`End()`)
  * `Rando/Logic/Metrics.cpp`               x4  (the Seed Metrics tab bar)
  * `UI/DeveloperTools/SaveEditor.cpp`      x5
  * `UI/DeveloperTools/GameplayTools.cpp`   x2
  * `UI/LighthouseModMenuWindow.cpp`        x2  (the texture-pack UI the README
                                                 tells users to open)
The Mod Menu pair matters as much as Rando's: a corrupted stack there would hit
anyone following the texture-pack instructions.

UPSTREAM BUG, not a porting artifact: no other patch in this overlay touches any
of these files, and the imbalance is plain in upstream's source. Worth reporting
upstream — the fix there is identical.

HOW THE FIX IS DERIVED, and why that is safe. The sites are found structurally
(brace matching) rather than by 17 hand-written string matches, then each one is
checked against four invariants before it is touched:

  1. exactly one terminator in the block,
  2. it is the LAST statement, immediately before the closing brace,
  3. the block closes on a bare `}` at the `if`'s own indent,
  4. no function-level `return` inside the block.

(1)-(3) make "move it past the brace" a pure relocation; (4) is what makes it
semantically identical — a `return` would skip the moved call. Three blocks do
contain jumps, all `continue` inside nested loops, which cannot escape the
block. The site count is asserted at 17, so an upstream bump that adds a site,
fixes one, or reshapes one fails the generator loudly instead of silently
leaving a broken menu behind (program ground rule 3).

Behaviour when the window IS visible is bit-identical. When it is culled, the
stack now unwinds correctly instead of corrupting the whole menu.
"""
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/Lighthouse"

# Where to look. Restricted to the port's own UI: LUS and the ImGui submodule
# are other patches' territory, and a stray hit there would be a different bug.
SCAN_ROOT = VENDOR / "src/port"
EXPECTED_SITES = 17

MARKER = "// LIGHTHOUSE_IOS (overlay 0047): terminator must be unconditional."

OPEN_RE = re.compile(r"^(\s*)if \(ImGui::(BeginChild|Begin)\(")


def find_sites(lines):
    """Yield (open_idx, close_idx, indent, terminator) for each offending block."""
    sites = []
    for i, line in enumerate(lines):
        m = OPEN_RE.match(line)
        if not m:
            continue
        indent, kind = m.group(1), m.group(2)
        term = "ImGui::EndChild();" if kind == "BeginChild" else "ImGui::End();"
        depth = 0
        started = False
        for j in range(i, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if "{" in lines[j]:
                started = True
            if started and depth <= 0:
                block = lines[i:j + 1]
                if any(term in b for b in block):
                    sites.append((i, j, indent, term, kind))
                break
    return sites


def check(cond, msg):
    assert cond, f"0047 invariant failed: {msg}"


def transform(path, text):
    lines = text.split("\n")
    sites = find_sites(lines)
    # Rewrite bottom-up so earlier indices stay valid.
    for i, j, indent, term, kind in reversed(sites):
        where = f"{path.relative_to(VENDOR)}:{i + 1}-{j + 1}"
        block = lines[i:j + 1]

        # (1) exactly one terminator
        n = sum(b.strip() == term for b in block)
        check(n == 1, f"{where}: expected 1 bare {term}, found {n}")
        # (2) it is the last statement
        check(lines[j - 1].strip() == term,
              f"{where}: {term} is not the last statement (found "
              f"{lines[j - 1].strip()!r})")
        # (3) the block closes on a bare brace at the if's indent
        check(lines[j] == indent + "}",
              f"{where}: block does not close on a bare '}}' at the if's indent "
              f"(found {lines[j]!r})")
        # (4) no function-level return could skip the moved terminator
        for b in block:
            check(not re.match(r"^\s*return\b", b),
                  f"{where}: block contains a return — moving {term} out would "
                  f"change behaviour")

        lines[j - 1:j + 1] = [
            indent + "}",
            indent + MARKER,
            indent + term,
        ]
    return len(sites), "\n".join(lines)


chunks = []
total = 0
for src in sorted(SCAN_ROOT.rglob("*.cpp")):
    orig = src.read_text()
    if "ImGui::Begin" not in orig:
        continue
    n, new = transform(src, orig)
    if n == 0:
        continue
    total += n
    rel = src.relative_to(VENDOR)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig)
        fb.write(new)
        fa.flush()
        fb.flush()
        r = subprocess.run(
            ["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
             fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: expected a diff, got rc={r.returncode}"
    chunks.append(r.stdout.decode())
    print(f"  {rel}: {n} site(s)")

assert total == EXPECTED_SITES, (
    f"expected {EXPECTED_SITES} offending sites, found {total}. Upstream moved: "
    f"re-run the structural scan, confirm every site is still the same shape, "
    f"and update EXPECTED_SITES deliberately.")

out = ROOT / "overlay/patches/0047-lighthouse-imgui-unconditional-end.patch"
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out}  ({total} sites across {len(chunks)} files)")
