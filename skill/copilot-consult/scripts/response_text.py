"""Conservative reconstruction of the observed Copilot code-line UI."""


def code_from_accessible_lines(rows):
    """Read alternating line-number/content Text nodes; reject gaps/ambiguity.

    The observed provider represents an empty code line by a single NBSP.
    Names preserve indentation where TextPattern mixes it with line numbers.
    """
    if not rows or len(rows)%2:
        raise ValueError("Code lines are incomplete or have an unknown structure")
    lines=[]
    for index in range(0,len(rows),2):
        number,content=rows[index:index+2]
        if number["type"]!="Text" or content["type"]!="Text" or number["name"]!=str(index//2+1):
            raise ValueError("Code line numbering is incomplete or virtualized; cannot claim complete code")
        value=content["name"]
        lines.append("" if value=="\u00a0" else value)
    return "\n".join(lines)
