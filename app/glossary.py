"""Small deterministic query expansion layer; extend from TR 21.905 later."""

ABBREVIATIONS = {
    "ue": "user equipment",
    "gnb": "gNodeB base station",
    "rrc": "radio resource control",
    "nr": "new radio 5G",
    "rlf": "radio link failure",
    "nas": "non-access stratum",
    "amf": "access and mobility management function",
    "smf": "session management function",
    "pdu": "protocol data unit",
    "drb": "data radio bearer",
    "srb": "signalling radio bearer",
    "sa": "standalone NR architecture",
    "nsa": "non-standalone NR architecture",
}

PHRASE_EXPANSIONS = {
    "rrc connection setup": "RRC connection establishment",
    "connection setup procedure": "connection establishment procedure",
}


def expand_abbreviations(query: str) -> str:
    lowered = query.lower()
    expansions = [f"{term}: {meaning}" for term, meaning in ABBREVIATIONS.items() if term in lowered.split()]
    phrase_expansions = [meaning for phrase, meaning in PHRASE_EXPANSIONS.items() if phrase in lowered]
    additions = expansions + phrase_expansions
    return query if not additions else f"{query}\n3GPP terminology: {'; '.join(additions)}"
