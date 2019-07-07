import os

from pathlib import Path

import pytest

from hypothesis import (assume, event, given, strategies as st, settings,
                        HealthCheck)

import nnf

from nnf import Var, And, Or, amc, dimacs, dsharp

settings.register_profile('patient', deadline=500)
settings.load_profile('patient')

a, b, c = Var('a'), Var('b'), Var('c')

fig1a = (~a & b) | (a & ~b)
fig1b = (~a | ~b) & (a | b)

uf20 = [
    dsharp.loads(file.open().read())
    for file in (Path(os.path.dirname(__file__))
                 / 'testdata' / 'satlib' / 'uf20').glob('*.nnf')
]


def test_all_models_basic():
    assert list(nnf.all_models([])) == [{}]
    assert list(nnf.all_models([1])) == [{1: False}, {1: True}]
    assert len(list(nnf.all_models(range(10)))) == 1024


@given(st.sets(st.integers(), max_size=8))
def test_all_models(names):
    result = list(nnf.all_models(names))
    # Proper result size
    assert len(result) == 2**len(names)
    # Only real names, only booleans
    assert all(name in names and isinstance(value, bool)
               for model in result
               for name, value in model.items())
    # Only complete models
    assert all(len(model) == len(names)
               for model in result)
    # No duplicate models
    assert len({tuple(model.items()) for model in result}) == len(result)


def test_basic():
    assert a.satisfied_by(dict(a=True))
    assert (a | b).satisfied_by(dict(a=False, b=True))
    assert not (a & b).satisfied_by(dict(a=True, b=False))

    assert (a & b).satisfiable()
    assert not (a & ~a).satisfiable()
    assert not (a & (~a & b)).satisfiable()

    assert ((a | b) & (b | c)).satisfiable()


def test_amc():
    assert amc.NUM_SAT(fig1a) == 2
    assert amc.NUM_SAT(fig1b) == 4

    assert amc.GRAD(a, {'a': 0.5}, 'a') == (0.5, 1)


names = st.integers(1, 8)


@st.composite
def variables(draw):
    return Var(draw(names), draw(st.booleans()))


@st.composite
def booleans(draw):
    return draw(st.sampled_from((nnf.true, nnf.false)))


@st.composite
def leaves(draw):
    return draw(st.one_of(variables(), booleans()))


@st.composite
def terms(draw):
    return And(Var(name, draw(st.booleans()))
               for name in draw(st.sets(names)))


@st.composite
def clauses(draw):
    return Or(Var(name, draw(st.booleans()))
              for name in draw(st.sets(names)))


@st.composite
def DNF(draw):
    return Or(draw(st.frozensets(terms())))


@st.composite
def CNF(draw):
    sentence = And(draw(st.frozensets(clauses())))
    assume(len(sentence.children) > 0)
    return sentence


@st.composite
def models(draw):
    return And(Var(name, draw(st.booleans()))
               for name in range(1, 9))


@st.composite
def MODS(draw):
    return Or(draw(st.frozensets(models())))


@st.composite
def internal(draw, children):
    return draw(st.sampled_from((And, Or)))(draw(st.frozensets(children)))


@st.composite
def NNF(draw):
    return draw(st.recursive(leaves(), internal))


@st.composite
def DNNF(draw):
    sentence = draw(NNF())
    assume(sentence.decomposable())
    return sentence


@given(DNF())
def test_hyp(sentence: nnf.Or):
    assume(len(sentence.children) != 0)
    assume(sentence.decomposable())
    assert sentence.satisfiable()
    assert sentence.vars() <= set(range(1, 9))


@given(MODS())
def test_MODS(sentence: nnf.Or):
    assert sentence.smooth()
    assert sentence.flat()
    assert sentence.decomposable()
    assert sentence.simply_conjunct()


@given(MODS())
def test_MODS_satisfiable(sentence: nnf.Or):
    assume(len(sentence.children) != 0)
    assert sentence.satisfiable()


@pytest.fixture(scope='module', params=[True, False])
def merge_nodes(request):
    return request.param


@settings(suppress_health_check=(HealthCheck.too_slow,))
@given(sentence=DNNF())
def test_DNNF_sat_strategies(sentence: nnf.NNF, merge_nodes):
    sat = sentence.satisfiable()
    if sat:
        assert sentence.simplify(merge_nodes) != nnf.false
        assert amc.SAT(sentence)
        event("Sentence satisfiable")
    else:
        assert sentence.simplify(merge_nodes) == nnf.false
        assert not amc.SAT(sentence)
        event("Sentence not satisfiable")


def test_amc_numsat():
    for sentence in uf20:
        assert (amc.NUM_SAT(sentence.make_smooth())
                == len(list(sentence.models())))


@given(sentence=NNF())
def test_idempotent_simplification(sentence: nnf.NNF, merge_nodes):
    sentence = sentence.simplify(merge_nodes)
    assert sentence.simplify(merge_nodes) == sentence


@given(sentence=NNF())
def test_simplify_preserves_meaning(sentence: nnf.NNF, merge_nodes):
    simple = sentence.simplify(merge_nodes)
    for model in sentence.models():
        assert simple.satisfied_by(model)
    for model in simple.models():
        assert sentence.condition(model).simplify(merge_nodes) == nnf.true


@given(sentence=NNF())
def test_simplify_eliminates_bools(sentence: nnf.NNF, merge_nodes):
    assume(sentence != nnf.true and sentence != nnf.false)
    if any(node == nnf.true or node == nnf.false
           for node in sentence.walk()):
        event("Sentence contained booleans originally")
    sentence = sentence.simplify(merge_nodes)
    if sentence == nnf.true or sentence == nnf.false:
        event("Sentence simplified to boolean")
    else:
        for node in sentence.walk():
            assert node != nnf.true and node != nnf.false


@given(NNF())
def test_simplify_merges_internal_nodes(sentence: nnf.NNF):
    if any(any(type(node) == type(child)
               for child in node.children)
           for node in sentence.walk()
           if isinstance(node, nnf.Internal)):
        event("Sentence contained immediately mergeable nodes")
        # Nodes may also be merged after intermediate nodes are removed
    for node in sentence.simplify().walk():
        if isinstance(node, nnf.Internal):
            for child in node.children:
                assert type(node) != type(child)


@given(sentence=DNNF())
def test_simplify_solves_DNNF_satisfiability(sentence: nnf.NNF, merge_nodes):
    if sentence.satisfiable():
        event("Sentence is satisfiable")
        assert sentence.simplify(merge_nodes) != nnf.false
    else:
        event("Sentence is not satisfiable")
        assert sentence.simplify(merge_nodes) == nnf.false


def test_dimacs_sat_serialize():
    # http://www.domagoj-babic.com/uploads/ResearchProjects/Spear/dimacs-cnf.pdf
    sample_input = """c Sample SAT format
c
p sat 4
(*(+(1 3 -4)
   +(4)
   +(2 3)))
"""
    assert dimacs.loads(sample_input) == And({
        Or({Var(1), Var(3), ~Var(4)}),
        Or({Var(4)}),
        Or({Var(2), Var(3)})
    })


@pytest.mark.parametrize(
    'serialized, sentence',
    [
        ('p sat 2\n(+((1)+((2))))', Or({Var(1), Or({Var(2)})}))
    ]
)
def test_dimacs_sat_weird_input(serialized: str, sentence: nnf.NNF):
    assert dimacs.loads(serialized) == sentence


def test_dimacs_cnf_serialize():
    sample_input = """c Example CNF format file
c
p cnf 4 3
1 3 -4 0
4 0 2
-3
"""
    assert dimacs.loads(sample_input) == And({
        Or({Var(1), Var(3), ~Var(4)}),
        Or({Var(4)}),
        Or({Var(2), ~Var(3)})
    })


@given(NNF())
def test_arbitrary_dimacs_sat_serialize(sentence: nnf.NNF):
    assert dimacs.loads(dimacs.dumps(sentence)) == sentence
    # Removing spaces may change the meaning, but shouldn't make it invalid
    # At least as far as our parser is concerned, a more sophisticated one
    # could detect variables with too high names
    serial = dimacs.dumps(sentence).split('\n')
    serial[1] = serial[1].replace(' ', '')
    dimacs.loads('\n'.join(serial))


@given(CNF())
def test_arbitrary_dimacs_cnf_serialize(sentence: nnf.And):
    assume(all(len(clause.children) > 0 for clause in sentence.children))
    assert dimacs.loads(dimacs.dumps(sentence, mode='cnf')) == sentence


@given(NNF())
def test_dimacs_cnf_serialize_accepts_only_cnf(sentence: nnf.NNF):
    if (isinstance(sentence, And)
            and all(isinstance(clause, Or)
                    and all(isinstance(var, Var)
                            for var in clause.children)
                    and len(clause.children) > 0
                    for clause in sentence.children)):
        event("CNF sentence")
        dimacs.dumps(sentence, mode='cnf')
    else:
        event("Not CNF sentence")
        with pytest.raises(TypeError):
            dimacs.dumps(sentence, mode='cnf')


@pytest.mark.parametrize(
    'fname, clauses',
    [
        ('bf0432-007.cnf', 3667),
        ('sw100-1.cnf', 3100),
        ('uuf250-01.cnf', 1065),
        ('uf20-01.cnf', 90),
    ]
)
def test_cnf_benchmark_data(fname: str, clauses: int):
    with open(os.path.dirname(__file__) + '/testdata/satlib/' + fname) as f:
        sentence = dimacs.load(f)
    assert isinstance(sentence, And) and len(sentence.children) == clauses


@pytest.mark.parametrize(
    'fname',
    [
        'uf20-01'
    ]
)
def test_dsharp_output(fname: str):
    basepath = os.path.dirname(__file__) + '/testdata/satlib/' + fname
    with open(basepath + '.nnf') as f:
        sentence = dsharp.load(f)
    with open(basepath + '.cnf') as f:
        clauses = dimacs.load(f)
    assert sentence.decomposable()
    # this is not a complete check, but clauses.models() is very expensive
    assert all(clauses.satisfied_by(model) for model in sentence.models())


@given(NNF())
def test_walk_unique_nodes(sentence: nnf.NNF):
    result = list(sentence.walk())
    assert len(result) == len(set(result))
    assert len(result) <= sentence.size() + 1


@given(st.dictionaries(st.integers(), st.booleans()))
def test_to_model(model: dict):
    sentence = nnf.And(nnf.Var(k, v) for k, v in model.items())
    assert sentence.to_model() == model


@given(NNF())
def test_models_smart_equivalence(sentence: nnf.NNF):
    dumb = list(sentence.models())
    smart = list(sentence._models_deterministic())
    assert model_set(dumb) == model_set(smart)


@pytest.mark.parametrize(
    'sentence, size',
    [
        ((a & b), 2),
        (a & (a | b), 4),
        ((a | b) & (~a | ~b), 6),
        (And({
            Or({a, b}),
            And({a, Or({a, b})}),
        }), 6)
    ]
)
def test_size(sentence: nnf.NNF, size: int):
    assert sentence.size() == size


@pytest.mark.parametrize(
    'a, b, contradictory',
    [
        (a, ~a, True),
        (a, b, False),
        (a, a, False),
        (a & b, a & ~b, True),
        (a & (a | b), b, False),
        (a & (a | b), ~a, True),
    ]
)
def test_contradicts(a: nnf.NNF, b: nnf.NNF, contradictory: bool):
    assert a.contradicts(b) == contradictory


@given(NNF())
def test_false_contradicts_everything(sentence: nnf.NNF):
    assert nnf.false.contradicts(sentence)


@given(DNNF())
def test_equivalent(sentence: nnf.NNF):
    assert sentence.equivalent(sentence)
    assert sentence.equivalent(sentence | nnf.false)
    assert not sentence.equivalent(sentence & nnf.Var('A'))
    if sentence.satisfiable():
        assert not sentence.equivalent(sentence & nnf.false)
    else:
        assert sentence.equivalent(sentence & nnf.false)


@given(NNF())
def test_smoothing(sentence: nnf.NNF):
    if not sentence.smooth():
        event("Sentence not smooth yet")
        smoothed = sentence.make_smooth()
        assert smoothed.smooth()
        assert sentence.equivalent(smoothed)
        assert smoothed.make_smooth() == smoothed
    else:
        event("Sentence already smooth")
        assert sentence.make_smooth() == sentence


def hashable_dict(model):
    return frozenset(model.items())


def model_set(model_gen):
    return frozenset(map(hashable_dict, model_gen))


def test_uf20_models():

    for sentence in uf20:
        assert sentence.decomposable()
        m = list(sentence.models(deterministic=False,
                                 decomposable=True))
        models = model_set(m)
        assert len(m) == len(models)
        assert models == model_set(sentence.models(deterministic=True,
                                                   decomposable=False))
        assert models == model_set(sentence.models(deterministic=True,
                                                   decomposable=True))


@given(NNF())
def test_deterministic_models_always_works(sentence: nnf.NNF):
    if sentence.deterministic():
        event("Sentence is deterministic")
    else:
        event("Sentence is not deterministic")
    with_det = list(sentence.models(deterministic=True))
    no_det = list(sentence.models(deterministic=False))
    assert len(with_det) == len(no_det)
    assert model_set(with_det) == model_set(no_det)


def test_instantiating_base_classes_fails():
    with pytest.raises(TypeError):
        nnf.NNF()
    with pytest.raises(TypeError):
        nnf.Internal()
    with pytest.raises(TypeError):
        nnf.Internal({nnf.Var(3)})


@given(NNF())
def test_negation(sentence: nnf.NNF):
    n_vars = len(sentence.vars())
    models_orig = model_set(sentence.models())
    models_negated = model_set(sentence.negate().models())
    assert len(models_orig) + len(models_negated) == 2**n_vars
    assert len(models_orig | models_negated) == 2**n_vars


@given(NNF())
def test_model_counting(sentence: nnf.NNF):
    assert sentence.model_count() == len(list(sentence.models()))


def test_uf20_model_counting():
    for sentence in uf20:
        assert (sentence.model_count(deterministic=True)
                == len(list(sentence.models())))


@given(NNF())
def test_validity(sentence: nnf.NNF):
    if sentence.valid():
        event("Valid sentence")
        assert all(sentence.satisfied_by(model)
                   for model in nnf.all_models(sentence.vars()))
    else:
        event("Invalid sentence")
        assert any(not sentence.satisfied_by(model)
                   for model in nnf.all_models(sentence.vars()))


def test_uf20_validity():
    for sentence in uf20:
        assert not sentence.valid(deterministic=True)


@given(CNF())
def test_is_CNF(sentence: nnf.NNF):
    assert sentence.is_CNF()
    assert not sentence.is_DNF()


@given(DNF())
def test_is_DNF(sentence: nnf.NNF):
    assert sentence.is_DNF()
    assert not sentence.is_CNF()


@given(NNF())
def test_to_MODS(sentence: nnf.NNF):
    assume(len(sentence.vars()) <= 5)
    mods = sentence.to_MODS()
    assert mods.is_MODS()
    assert isinstance(mods, Or)
    assert mods.model_count() == len(mods.children)


@given(MODS())
def test_is_MODS(sentence: nnf.NNF):
    assert sentence.is_MODS()
