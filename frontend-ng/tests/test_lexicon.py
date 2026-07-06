"""The lexical normalization layer: stemming, synonym/phrase expansion, and the invariant
that every synonym target is real GINI vocabulary (so the hand-owned map can't rot silently)."""
from gini.agent.embed import kb_documents
from gini.domain import lexicon as lex


def test_stemming_folds_inflections():
    # plurals and verb inflections collapse to one stem so query/doc tokens meet
    assert lex.normalize("routers")[0] == lex.normalize("router")[0]
    assert lex.normalize("switches")[0] == lex.normalize("switch")[0]
    assert lex.normalize("routing")[0] == lex.normalize("routers")[0]


def test_query_side_drops_noise_words():
    toks = lex.normalize("explain what SDN is", query=True)
    assert "sdn" in toks
    assert "explain" not in toks and "what" not in toks


def test_single_token_synonyms():
    # a synonym injects the canonical GINI term (compared as stems, since both sides stem)
    def has(word, text):
        return lex.normalize(word)[0] in lex.normalize(text)

    assert has("instance", "spin up a vm")
    assert has("function", "write a lambda")
    assert has("database", "connect to postgres")


def test_multiword_phrase_expansion():
    # the exact case that exposed lexical brittleness: "software defined" ↔ SDN
    assert "sdn" in lex.normalize("how does software defined networking work")
    assert "queue" in lex.normalize("set up a message bus")


def test_every_synonym_target_is_real_kb_vocabulary():
    vocab = set()
    for _, text in kb_documents():
        vocab.update(lex.normalize(text))
    missing = {t for t in lex.synonym_targets() if lex.normalize(t) and lex.normalize(t)[0] not in vocab}
    assert not missing, f"synonym targets not found in the KB: {sorted(missing)}"
