import sympy
import torch
from .losses import Loss


class LogProb(Loss):
    r"""
    The log probability density/mass function.

    .. math::

        \log p(x)
    """

    def __init__(self, p, sum_features=True, feature_dims=None):
        input_var = p.var + p.cond_var
        self.sum_features = sum_features
        self.feature_dims = feature_dims
        super().__init__(p, input_var=input_var)

    def get_symbol(self, add_index=False):
        return sympy.Symbol("\\log {}".format(self._p.get_prob_text(add_index)))

    def get_eval(self, x={}, **kwargs):
        log_prob = self._p.get_log_prob(x, sum_features=self.sum_features, feature_dims=self.feature_dims)
        return log_prob, x


class Prob(LogProb):
    r"""
    The probability density/mass function.

    .. math::

        p(x) = \exp(\log p(x))
    """

    def get_symbol(self, add_index=False):
        return sympy.Symbol(self._p.get_prob_text(add_index))

    def get_eval(self, x={}, **kwargs):
        log_prob, x = super()._get_eval(x, **kwargs)
        return torch.exp(log_prob), x
