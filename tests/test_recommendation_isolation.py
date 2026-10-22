import subprocess
import sys
import unittest


class RecommendationIsolationTests(unittest.TestCase):
    def test_cli_and_http_adapter_work_without_research_imports(self):
        for mode in ('cli', 'http'):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, '-c', '''
import importlib.abc
import json
import sys
import tempfile
from pathlib import Path

blocked = {'backend.prediction.' + name for name in (
    'recommendations_research', 'experiment', 'evaluation', 'retrospective',
    'tuning', 'quality', 'outcomes', 'recommendation_comparison', 'baseline')}

class InferenceOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked or fullname == 'sklearn' or fullname.startswith('sklearn.'):
            raise ImportError('Research import during inference: ' + fullname)

sys.meta_path.insert(0, InferenceOnly())
from backend.prediction.artifacts import ResearchModels, Tree
from backend.prediction.recommendations import ModelArtifact, code_hashes, load_model, main
from backend.review import recommend_claim

tree = Tree(left=(-1,), right=(-1,), feature=(-2,), threshold=(-2,), value=(0,))
models = ResearchModels(feature_names=('rent',), means=(0,), scales=(1,), decision=tree,
    ceiling=(tree,), initial_ceiling=500, learning_rate=.1,
    training_median=300, majority_decision='approved')
hashes = code_hashes()
assert {'recommendations_research.py', 'features.py', 'parsing.py'} <= hashes.keys()
artifact = ModelArtifact(models=models, source_hashes={}, code_hashes=hashes,
    development_ids=('100',), reserved_ids=('999',), environment={}, training_parameters={})
values = {'Monthly Rent': '1000', 'Amount of Claim': '900', 'Max Benefit': '1500',
    'Claim Date': '10/01/24', 'Lease Start Date': '10/01/23',
    'Lease End Date': '09/01/24', 'Move-Out Date': '09/01/24', 'Termination Type': 'Early'}
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    model, inputs, output = (root / name for name in ('model.json', 'input.json', 'output.json'))
    model.write_text(artifact.model_dump_json())
    import hashlib
    expected_hash = hashlib.sha256(model.read_bytes()).hexdigest()
    loaded = load_model(model, expected_hash)
    if sys.argv[1] == 'cli':
        inputs.write_text(json.dumps({'claim_id': '100', 'values': values, 'source_reference': 'synthetic'}))
        sys.argv = ['recommendations', 'recommend', str(model), str(inputs),
                    '--model-sha256', expected_hash, '--output', str(output)]
        main()
        result = json.loads(output.read_text())
    else:
        claim = {'tracking_number': '100', 'claims_source': {'id': 'synthetic'},
                 'prediction_inputs': {key: {'source_column': key, 'recorded_value': value}
                                       for key, value in values.items()},
                 'historical_outcome': {'Status': 'Approved Revised', 'Approved Benefit Amount': '500'}}
        result = recommend_claim(claim, loaded, expected_hash)
        assert result['historical_comparison']['difference_dollars'] == 0
        assert result['historical_comparison']['decision_match'] is True
    assert result['research_decision'] == 'approved'
    assert result['conditional_ceiling'] == 500
    assert result['model_sha256'] == expected_hash
assert not blocked.intersection(sys.modules)
assert not any(name == 'sklearn' or name.startswith('sklearn.') for name in sys.modules)
''', mode], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
