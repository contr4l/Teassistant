"""Source-bound native mathematics converted to actual LaTeX, never preview images."""

import re
from typing import Literal

from pydantic import Field, model_validator

from app.contracts import Contract

_COMMANDS = frozenset(
    """
frac dfrac tfrac sqrt left right middle big Big bigg Bigg bigl bigr Bigl Bigr
cdot times div pm mp circ degree angle triangle odot bigcirc perp parallel
le leq ge geq ne neq approx sim simeq cong equiv propto in notin ni subset supset
subseteq supseteq cup cap emptyset varnothing forall exists neg land lor
because therefore implies iff to rightarrow leftarrow leftrightarrow
Rightarrow Leftarrow Leftrightarrow mapsto longrightarrow longleftarrow
infty partial nabla ell hbar prime dots ldots cdots vdots ddots
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
sum prod int iint oint lim max min sup inf sin cos tan cot sec csc arcsin arccos
arctan log ln exp det gcd dim mod bmod pmod operatorname mathop limits nolimits
mathrm mathit mathbf mathbb mathcal mathscr mathfrak mathsf mathtt mathnormal boldsymbol
text textrm textit textbf mbox phantom hphantom vphantom smash strut mathstrut
overline underline overrightarrow overleftarrow overleftrightarrow
widehat widetilde hat tilde bar vec dot ddot acute grave breve check
overset underset stackrel overbrace underbrace substack displaystyle textstyle
scriptstyle scriptscriptstyle begin end quad qquad thinspace medspace thickspace
enspace lvert rvert lVert rVert vert Vert langle rangle lbrace rbrace lceil rceil
lfloor rfloor backslash percent textbackslash textasciitilde textasciicircum
mathord mathrel mathbin mathopen mathclose mathpunct mathinner
not colon ast star bullet diamond square Box checkmark top bot
""".split()
)
_ENVIRONMENTS = frozenset(
    {
        "matrix",
        "pmatrix",
        "bmatrix",
        "Bmatrix",
        "vmatrix",
        "Vmatrix",
        "cases",
        "aligned",
        "gathered",
        "smallmatrix",
        "array",
    }
)


def validate_math_latex(value: str) -> str:
    """Permit mathematical markup, rejecting document commands and file/process access."""
    if (
        not value.strip()
        or len(value) > 50000
        or any(ord(ch) < 32 and ch not in "\n\t\r" for ch in value)
    ):
        raise ValueError("empty or invalid LaTeX mathematics")
    if "^^" in value:
        raise ValueError("TeX input character recoding is not mathematical markup")
    tokens = re.findall(r"\\[A-Za-z@]+|\\.|.", value, re.DOTALL)
    if any(token in {"$", "%", "#", "\\"} for token in tokens):
        raise ValueError("math fragments cannot contain raw TeX control characters")
    for command in re.findall(r"\\([A-Za-z@]+)", value):
        if command not in _COMMANDS:
            raise ValueError(f"unsupported math command: {command}")
    environments = re.findall(r"\\(?:begin|end)\s*\{([^{}]+)\}", value)
    if any(name not in _ENVIRONMENTS for name in environments):
        raise ValueError("unsupported math environment")
    depth = 0
    for token in re.findall(r"\\.|[{}]", value):
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        if depth < 0:
            raise ValueError("unbalanced math braces")
    if depth:
        raise ValueError("unbalanced math braces")
    if re.search(r"[\ue000-\uf8ff\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]", value):
        raise ValueError("unmapped private-use math character")
    return value


class FormulaConversion(Contract):
    source_kind: Literal["ole", "omml"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_payload_sha256: str | None = None
    status: Literal["converted", "unsupported"]
    latex: str | None = None
    mathml: str | None = None
    converter: str
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistency(self):
        if self.status == "converted":
            if self.errors or self.latex is None:
                raise ValueError("converted formula needs mathematical LaTeX and no errors")
            validate_math_latex(self.latex)
        elif self.latex is not None or not self.errors:
            raise ValueError("unsupported formula needs an error and cannot claim LaTeX")
        return self


class FormulaCatalog(Contract):
    schema_version: Literal["latex-formula-catalog-v1"] = "latex-formula-catalog-v1"
    snapshot_id: str
    bank_sha256: str
    entries: dict[str, FormulaConversion]

    @model_validator(mode="after")
    def consistency(self):
        if any(key != item.source_sha256 for key, item in self.entries.items()):
            raise ValueError("formula catalog key differs from source hash")
        return self
