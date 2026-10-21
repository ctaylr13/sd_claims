"""Portable JSON for the fixed research tree and ceiling ensemble."""

import numpy as np
from pydantic import Field, model_validator

from .models import FrozenModel


class Tree(FrozenModel):
    left: tuple[int, ...]
    right: tuple[int, ...]
    feature: tuple[int, ...]
    threshold: tuple[float, ...]
    value: tuple[float, ...]

    @model_validator(mode='after')
    def valid_nodes(self):
        size = len(self.left)
        if not size or any(len(values) != size for values in (
                self.right, self.feature, self.threshold, self.value)):
            raise ValueError('Invalid tree arrays')
        if not np.isfinite(self.threshold + self.value).all():
            raise ValueError('Nonfinite tree value')
        for node, (left, right) in enumerate(zip(self.left, self.right)):
            if left == right == -1:
                continue
            if not (node < left < size and node < right < size and self.feature[node] >= 0):
                raise ValueError('Invalid tree branch')
        return self


class ResearchModels(FrozenModel):
    version: str = 'research-models-1'
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    decision: Tree
    ceiling: tuple[Tree, ...] = Field(min_length=1)
    initial_ceiling: float
    learning_rate: float = Field(gt=0)
    training_median: float = Field(ge=0)
    majority_decision: str

    @model_validator(mode='after')
    def valid_features(self):
        size = len(self.feature_names)
        if self.version != 'research-models-1' or not size or len(set(self.feature_names)) != size:
            raise ValueError('Invalid feature schema')
        if len(self.means) != size or len(self.scales) != size:
            raise ValueError('Invalid preprocessing dimensions')
        if not np.isfinite(self.means + self.scales + (
                self.initial_ceiling, self.learning_rate, self.training_median)).all() or min(self.scales) <= 0:
            raise ValueError('Invalid preprocessing values')
        if self.majority_decision not in {'approved', 'declined'}:
            raise ValueError('Invalid majority decision')
        for tree in (self.decision, *self.ceiling):
            if any(feature >= size for feature in tree.feature):
                raise ValueError('Tree feature outside vocabulary')
        if any(value not in (0, 1) for value in self.decision.value):
            raise ValueError('Invalid decision class')
        return self


def _tree(estimator, *, decision=False):
    tree = estimator.tree_
    values = (np.argmax(tree.value[:, 0, :], axis=1) if decision else tree.value[:, 0, 0])
    return Tree(left=tuple(tree.children_left), right=tuple(tree.children_right),
                feature=tuple(tree.feature), threshold=tuple(tree.threshold), value=tuple(values))


def export_models(decision, ceiling, labels, majority):
    if list(decision[-1].classes_) != ['approved', 'declined']:
        raise ValueError('Both decision classes required')
    if decision[0].vocabulary_ != ceiling[0].vocabulary_:
        raise ValueError('Preprocessing vocabularies differ')
    if not np.array_equal(decision[1].mean_, ceiling[1].mean_) or not np.array_equal(
            decision[1].scale_, ceiling[1].scale_):
        raise ValueError('Preprocessing scales differ')
    estimator = ceiling[-1]
    return ResearchModels(feature_names=tuple(decision[0].get_feature_names_out()),
        means=tuple(decision[1].mean_), scales=tuple(decision[1].scale_), decision=_tree(decision[-1], decision=True),
        ceiling=tuple(_tree(tree) for tree in estimator.estimators_[:, 0]),
        initial_ceiling=float(estimator.init_.constant_[0, 0]), learning_rate=estimator.learning_rate,
        training_median=float(np.median(labels)), majority_decision=majority)


def _value(tree, values):
    node = 0
    while tree.left[node] != -1:
        node = tree.left[node] if float(values[tree.feature[node]]) <= tree.threshold[node] else tree.right[node]
    return tree.value[node]


def predict(models, features):
    values = np.asarray([features.get(name, 0.0) for name in models.feature_names])
    values = ((values - models.means) / models.scales).astype(np.float32)
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite model input')
    decision = 'declined' if _value(models.decision, values) else 'approved'
    ceiling = models.initial_ceiling + sum(models.learning_rate * _value(tree, values) for tree in models.ceiling)
    return decision, ceiling
