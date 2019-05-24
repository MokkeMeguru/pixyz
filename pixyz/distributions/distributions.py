from __future__ import print_function
import torch
import numbers
import re
from torch import nn
from copy import deepcopy

from ..utils import get_dict_values, replace_dict_keys, replace_dict_keys_split, delete_dict_values,\
    tolist, sum_samples
from ..losses import LogProb, Prob


class Distribution(nn.Module):
    """Distribution class. In Pixyz, all distributions are required to inherit this class."""

    def __init__(self, var, cond_var=[], name="p", dim=1):
        """
        Parameters
        ----------
        var : :obj:`list` of :obj:`str`
            Variables of this distribution.
        cond_var : :obj:`list` of :obj:`str`, defaults to []
            Conditional variables of this distribution.
            In case that cond_var is not empty, we must set the corresponding inputs to sample variables.
        name : :obj:`str`, defaults to "p"
            Name of this distribution.
            This name is displayed in :attr:`prob_text` and :attr:`prob_factorized_text`.
        dim : :obj:`int`, defaults to 1
            Number of dimensions of this distribution.
            This might be ignored depending on the shape which is set in the sample method and on its parent
            distribution.
            Moreover, this is not consider when this class is inherited by DNNs.

        """
        super().__init__()

        _vars = cond_var + var
        if len(_vars) != len(set(_vars)):
            raise ValueError("There are conflicted variables.")

        self._cond_var = cond_var
        self._var = var
        self._dim = dim
        self._name = name

        self._prob_text = None
        self._prob_factorized_text = None

        self._index = []

    @property
    def distribution_name(self):
        """str: Name of this distribution class."""
        raise NotImplementedError

    @property
    def name(self):
        """str: Name of this distribution displayed in :obj:`prob_text` and :obj:`prob_factorized_text`."""
        return self._name

    @name.setter
    def name(self, name):
        if type(name) is str:
            self._name = name
            return

        raise ValueError("Name of the distribution class must be a string type.")

    @property
    def var(self):
        """list: Variables of this distribution."""
        return self._var

    @property
    def cond_var(self):
        """list: Conditional variables of this distribution."""
        return self._cond_var

    @property
    def input_var(self):
        """list: Input variables of this distribution.
        Normally, it has same values as :attr:`cond_var`.

        """
        return self._cond_var

    def get_prob_text(self, add_index=False):
        """str: Return a formula of the (joint) probability distribution."""

        if add_index and (len(self.index) != 0):
            index = "_{" + ','.join(self.index) + "}"
        else:
            index = ""

        var_with_index = [var + index for var in self.var]
        cond_var_with_index = [cond_var + index for cond_var in self.cond_var]

        _var_text = [','.join(var_with_index)]
        if len(cond_var_with_index) != 0:
            _var_text += [','.join(cond_var_with_index)]

        _prob_text = "{}({})".format(
            self._name,
            "|".join(_var_text)
        )

        return _prob_text

    def get_prob_factorized_text(self, add_index=False):
        """str: Return a formula of the factorized probability distribution."""
        return self.get_prob_text(add_index)

    def get_prob_joint_factorized_and_text(self, add_index=None):
        """str: Return a formula of the factorized probability distribution."""
        prob_text = self.get_prob_text(add_index)
        prob_factorized_text = self.get_prob_factorized_text(add_index)
        if prob_text == prob_factorized_text:
            prob_joint_factorized_and_text = prob_text
        else:
            prob_joint_factorized_and_text = "{} = {}".format(prob_text, prob_factorized_text)
        return prob_joint_factorized_and_text

    @property
    def dim(self):
        """int: Number of dimensions of this distribution."""
        return self._dim

    @property
    def index(self):
        """list: Input variables of this distribution."""
        return self._index

    @index.setter
    def index(self, index):
        if type(index) is list:
            self._index = index
            return

        raise ValueError("Index must be a list type.")

    def _check_input(self, x, var=None):
        """Check the type of given input.
        If the input type is :obj:`dict`, this method checks whether the input keys contains the :attr:`var` list.
        In case that its type is :obj:`list` or :obj:`tensor`, it returns the output formatted in :obj:`dict`.

        Parameters
        ----------
        x : :obj:`torch.Tensor`, :obj:`list`, or :obj:`dict`
            Input variables.
        var : :obj:`list` or :obj:`NoneType`, defaults to None
            Variables to check if given input contains them.
            This is set to None by default.

        Returns
        -------
        checked_x : dict
            Variables checked in this method.

        Raises
        ------
        ValueError
            Raises `ValueError` if the type of input is neither :obj:`torch.Tensor`, :obj:`list`, nor :obj:`dict.

        """
        if var is None:
            var = self.input_var

        if type(x) is torch.Tensor:
            checked_x = {var[0]: x}

        elif type(x) is list:
            # TODO: we need to check if all the elements contained in this list are torch.Tensor.
            checked_x = dict(zip(var, x))

        elif type(x) is dict:
            if not (set(list(x.keys())) >= set(var)):
                raise ValueError("Input keys are not valid.")
            checked_x = x

        else:
            raise ValueError("The type of input is not valid, got %s." % type(x))

        return checked_x

    def get_params(self, params_dict={}):
        """This method aims to get parameters of this distributions from constant parameters set in initialization
        and outputs of DNNs.

        Parameters
        ----------
        params_dict : :obj:`dict`, defaults to {}
            Input parameters.

        Returns
        -------
        output_dict : dict
            Output parameters.

        Examples
        --------
        >>> from pixyz.distributions import Normal
        >>> dist_1 = Normal(loc=0, scale=1, var=["x"], dim=1)
        >>> print(dist_1.prob_text, dist_1.distribution_name)
        p(x) Normal
        >>> dist_1.get_params()
        {'loc': 0, 'scale': 1}
        >>> dist_2 = Normal(loc=0, scale="z", cond_var=["z"], var=["x"])
        >>> print(dist_2.prob_text, dist_2.distribution_name)
        p(x|z) Normal
        >>> dist_2.get_params({"z": 1})
        {'scale': 1, 'loc': 0}

        """
        raise NotImplementedError

    def sample(self, x={}, shape=None, batch_size=1, return_all=True,
               reparam=False):
        """Sample variables of this distribution.
        If :attr:`cond_var` is not empty, you should set inputs as :obj:`dict`.

        Parameters
        ----------
        x : :obj:`torch.Tensor`, :obj:`list`, or :obj:`dict`, defaults to {}
            Input variables.
        shape : :obj:`tuple` or :obj:`NoneType`, defaults to None
            Shape of samples.
            If set, :attr:`batch_size` is ignored.
        batch_size : :obj:`int`, defaults to 1.
            Batch size of samples.
        return_all : :obj:`bool`, defaults to True
            Choose whether the output contains input variables.
        reparam : :obj:`bool`, defaults to False.
            Choose whether we sample variables with re-parameterized trick.

        Returns
        -------
        output : dict
            Samples of this distribution.

        """
        raise NotImplementedError

    def sample_mean(self, x={}):
        """Return the mean of the distribution.

        Parameters
        ----------
        x : :obj:`dict`, defaults to {}
            Parameters of this distribution.

        """
        raise NotImplementedError

    def sample_variance(self, x={}):
        """Return the variance of the distribution.

        Parameters
        ----------
        x : :obj:`dict`, defaults to {}
            Parameters of this distribution.

        """
        raise NotImplementedError

    def get_log_prob(self, x_dict, sum_features=True, feature_dims=None):
        """Giving variables, this method returns values of log-pdf.

        Parameters
        ----------
        x_dict : dict
            Input variables.
        sum_features : :obj:`bool`, defaults to True
            Whether the output is summed across some axes (dimensions) which are specified by `feature_dims`.
        feature_dims : :obj:`list` or :obj:`NoneType`, defaults to None
            Set axes to sum across the output.

        Returns
        -------
        log_prob : torch.Tensor
            Values of log-probability density/mass function.

        """
        raise NotImplementedError

    def log_prob(self, sum_features=True, feature_dims=None):
        """Return an instance of :class:`pixyz.losses.LogProb`.

        Parameters
        ----------
        sum_features : :obj:`bool`, defaults to True
            Whether the output is summed across some axes (dimensions) which are specified by :attr:`feature_dims`.
        feature_dims : :obj:`list` or :obj:`NoneType`, defaults to None
            Set axes to sum across the output.

        Returns
        -------
        pixyz.losses.LogProb
            An instance of :class:`pixyz.losses.LogProb`

        """
        return LogProb(self, sum_features=sum_features, feature_dims=feature_dims)

    def prob(self, sum_features=True, feature_dims=None):
        """Return an instance of :class:`pixyz.losses.LogProb`.

        Parameters
        ----------
        sum_features : :obj:`bool`, defaults to True
            Choose whether the output is summed across some axes (dimensions)
            which are specified by :attr:`feature_dims`.
        feature_dims : :obj:`list` or :obj:`NoneType`, defaults to None
            Set axes to sum across the output.

        Returns
        -------
        pixyz.losses.Prob
            An instance of :class:`pixyz.losses.Prob`

        """
        return Prob(self, sum_features=sum_features, feature_dims=feature_dims)

    def forward(self, *args, **kwargs):
        """When this class is inherited by DNNs, this method should be overrided."""

        raise NotImplementedError

    def replace_var(self, **replace_dict):
        """Return an instance of :class:`pixyz.distributions.ReplaceVarDistribution`.

        Parameters
        ----------
        replace_dict : dict
            Dictionary.

        Returns
        -------
        pixyz.distributions.ReplaceVarDistribution
            An instance of :class:`pixyz.distributions.ReplaceVarDistribution`

        """

        return ReplaceVarDistribution(self, replace_dict)

    def marginalize_var(self, marginalize_list):
        """Return an instance of :class:`pixyz.distributions.MarginalizeVarDistribution`.

        Parameters
        ----------
        marginalize_list : :obj:`list` or other
            Variables to marginalize.

        Returns
        -------
        pixyz.distributions.MarginalizeVarDistribution
            An instance of :class:`pixyz.distributions.MarginalizeVarDistribution`

        """

        marginalize_list = tolist(marginalize_list)
        return MarginalizeVarDistribution(self, marginalize_list)

    def __mul__(self, other):
        return MultiplyDistribution(self, other)

    def __str__(self):
        # Distribution
        text = "Distribution:\n  {}\n".format(self.get_prob_joint_factorized_and_text(False))

        # Network architecture (`repr`)
        network_text = self.__repr__()
        network_text = re.sub('^', ' ' * 2, str(network_text), flags=re.MULTILINE)
        text += "Network architecture:\n{}".format(network_text)
        return text

    def __repr__(self):
        text = "{} ({}): {}".format(self.get_prob_text(False), self.distribution_name, super().__repr__())
        return text


class DistributionBase(Distribution):
    """Distribution class with PyTorch. In Pixyz, all distributions are required to inherit this class."""

    def __init__(self, cond_var=[], var=["x"], name="p", dim=1, **kwargs):
        super().__init__(cond_var=cond_var, var=var, name=name, dim=dim)

        self._set_constant_params(**kwargs)
        self._dist = None

    def _set_constant_params(self, **params_dict):
        """Format constant parameters of this distribution.

        Parameters
        ----------
        params_dict : dict
            Constant parameters of this distribution set at initialization.
            If the values of these dictionaries contain parameters which are named as strings, which means that
            these parameters are set as `variables`, the correspondences between these values and the true name of
            these parameters are stored as :obj:`dict` (:attr:`replace_params_dict`).

        """

        self.replace_params_dict = {}
        self.constant_params_dict = {}

        for key in params_dict.keys():
            if type(params_dict[key]) is str:
                if params_dict[key] in self._cond_var:
                    self.replace_params_dict[params_dict[key]] = key
                else:
                    raise ValueError
            elif isinstance(params_dict[key], numbers.Number) or isinstance(params_dict[key], torch.Tensor):
                self.constant_params_dict[key] = params_dict[key]
            else:
                raise ValueError

    @property
    def params_keys(self):
        """list: Return the list of parameter names for this distribution."""
        raise NotImplementedError

    @property
    def distribution_torch_class(self):
        """Return the class of PyTorch distribution."""
        raise NotImplementedError

    @property
    def dist(self):
        """Return the instance of PyTorch distribution."""
        return self._dist

    def set_dist(self, x={}, sampling=False, **kwargs):
        """Set :attr:`dist` as PyTorch distributions given parameters.

        This requires that :attr:`params_keys` and :attr:`distribution_torch_class` are set.

        Parameters
        ----------
        x : :obj:`dict`, defaults to {}.
            Parameters of this distribution.
        sampling : :obj:`bool`, defaults to False.
            Choose whether to use relaxed_* in PyTorch distribution.
        **kwargs
            Arbitrary keyword arguments.

        Returns
        -------

        """
        params = self.get_params(x, **kwargs)
        if set(self.params_keys) != set(params.keys()):
            raise ValueError

        self._dist = self.distribution_torch_class(**params)

    def get_sample(self, reparam=False, sample_shape=torch.Size()):
        """Get a sample_shape shaped sample from :attr:`dist`.

        Parameters
        ----------
        reparam : :obj:`bool`, defaults to True.
            Choose where to sample using re-parameterization trick.

        sample_shape : :obj:`tuple` or :obj:`torch.Size`, defaults to torch.Size().
            Set the shape of a generated sample.

        Returns
        -------
        samples_dict : dict
            Generated sample formatted by :obj:`dict`.

        """
        if reparam:
            try:
                _samples = self.dist.rsample(sample_shape=sample_shape)
            except NotImplementedError:
                print("You cannot use the re-parameterization trick for this distribution.")
        else:
            _samples = self.dist.sample(sample_shape=sample_shape)
        samples_dict = {self._var[0]: _samples}

        return samples_dict

    def get_log_prob(self, x_dict, sum_features=True, feature_dims=None):
        _x_dict = get_dict_values(x_dict, self._cond_var, return_dict=True)
        self.set_dist(_x_dict, sampling=False)

        x_targets = get_dict_values(x_dict, self._var)
        log_prob = self.dist.log_prob(*x_targets)
        if sum_features:
            log_prob = sum_samples(log_prob)

        return log_prob

    def get_params(self, params_dict={}):
        params_dict, vars_dict = replace_dict_keys_split(params_dict, self.replace_params_dict)
        output_dict = self.forward(**vars_dict)

        # append constant_params to dict
        output_dict.update(params_dict)
        output_dict.update(self.constant_params_dict)

        return output_dict

    def sample(self, x={}, shape=None, batch_size=1, return_all=True, reparam=False):
        # check whether the input is valid or convert it to valid dictionary.
        x_dict = self._check_input(x)

        # unconditioned
        if len(self.input_var) == 0:
            if shape:
                sample_shape = shape
            else:
                if self.dim is None:
                    sample_shape = (batch_size, )
                else:
                    sample_shape = (batch_size, self.dim)

            self.set_dist()
            output_dict = self.get_sample(reparam=reparam,
                                          sample_shape=sample_shape)

        # conditioned
        else:
            # remove redundant variables from x_dict.
            _x_dict = get_dict_values(x_dict, self.input_var, return_dict=True)
            self.set_dist(_x_dict)
            output_dict = self.get_sample(reparam=reparam)

        if return_all:
            x_dict.update(output_dict)
            return x_dict

        return output_dict

    def sample_mean(self, x={}):
        self.set_dist(x)
        return self.dist.mean

    def sample_variance(self, x={}):
        self.set_dist(x)
        return self.dist.variance

    def forward(self, **params):
        return params


class MultiplyDistribution(Distribution):
    """Multiply by given distributions, e.g, :math:`p(x,y|z) = p(x|z,y)p(y|z)`.
    In this class, it is checked if two distributions can be multiplied.

    p(x|z)p(z|y) -> Valid

    p(x|z)p(y|z) -> Valid

    p(x|z)p(y|a) -> Valid

    p(x|z)p(z|x) -> Invalid (recursive)

    p(x|z)p(x|y) -> Invalid (conflict)

    Examples
    --------
    >>> a = DistributionBase(var=["x"], cond_var=["z"])
    >>> b = DistributionBase(var=["z"], cond_var=["y"])
    >>> p_multi = MultiplyDistribution(a, b)
    >>> print(p_multi.prob_text, p_multi.prob_factorized_text)
    p(x,z|y) p(x|z)p(z|y)
    >>> b = DistributionBase(var=["y"], cond_var=["z"])
    >>> p_multi = MultiplyDistribution(a, b)
    >>> print(p_multi.prob_text, p_multi.prob_factorized_text)
    p(x,y|z) p(x|z)p(y|z)
    >>> b = DistributionBase(var=["y"], cond_var=["a"])
    >>> p_multi = MultiplyDistribution(a, b)
    >>> print(p_multi.prob_text, p_multi.prob_factorized_text)
    p(x,y|z,a) p(x|z)p(y|a)

    """

    def __init__(self, a, b):
        """
        Parameters
        ----------
        a : pixyz.Distribution
            Distribution.

        b : pixyz.Distribution
            Distribution.

        """
        if not (isinstance(a, Distribution) and isinstance(b, Distribution)):
            raise ValueError("Given inputs should be `pixyz.Distribution`, got {} and {}.".format(type(a), type(b)))

        # Check parent-child relationship between two distributions.
        # If inherited variables (`_inh_var`) are exist (e.g. c in p(e|c)p(c|a,b)),
        # then p(e|c) is a child and p(c|a,b) is a parent, otherwise it is opposite.
        _vars_a_b = a.cond_var + b.var
        _vars_b_a = b.cond_var + a.var
        _inh_var_a_b = [var for var in set(_vars_a_b) if _vars_a_b.count(var) > 1]
        _inh_var_b_a = [var for var in set(_vars_b_a) if _vars_b_a.count(var) > 1]

        if len(_inh_var_a_b) > 0:
            _child = a
            _parent = b
            _inh_var = _inh_var_a_b

        elif len(_inh_var_b_a) > 0:
            _child = b
            _parent = a
            _inh_var = _inh_var_b_a

        else:
            _child = a
            _parent = b
            _inh_var = []

        # Check if variables of two distributions are "recursive" (e.g. p(x|z)p(z|x)).
        _check_recursive_vars = _child.var + _parent.cond_var
        if len(_check_recursive_vars) != len(set(_check_recursive_vars)):
            raise ValueError("Variables of two distributions, {} and {}, are recursive.".format(_child.prob_text,
                                                                                                _parent.prob_text))

        # Set variables.
        _var = _child.var + _parent.var
        if len(_var) != len(set(_var)):  # e.g. p(x|z)p(x|y)
            raise ValueError("Variables of two distributions, {} and {}, are conflicted.".format(_child.prob_text,
                                                                                                 _parent.prob_text))

        # Set conditional variables.
        _cond_var = _child.cond_var + _parent.cond_var
        _cond_var = sorted(set(_cond_var), key=_cond_var.index)

        # Delete inh_var in conditional variables.
        _cond_var = [var for var in _cond_var if var not in _inh_var]

        super().__init__(cond_var=_cond_var, var=_var)

        self._parent = _parent
        self._child = _child

        # Set input_var (it might be different from cond_var if either a and b contain data distributions.)
        _input_var = [var for var in self._child.input_var if var not in _inh_var]
        _input_var += self._parent.input_var
        self._input_var = sorted(set(_input_var), key=_input_var.index)

    @property
    def input_var(self):
        return self._input_var

    @property
    def prob_factorized_text(self):
        return self._child.prob_factorized_text + self._parent.prob_factorized_text

    def sample(self, x={}, shape=None, batch_size=1, return_all=True, reparam=False):
        # sample from the parent distribution
        parents_x_dict = x
        child_x_dict = self._parent.sample(x=parents_x_dict,
                                           shape=shape,
                                           batch_size=batch_size,
                                           return_all=True, reparam=reparam)

        # sample from the child distribution
        output_dict = self._child.sample(x=child_x_dict,
                                         shape=shape,
                                         batch_size=batch_size,
                                         return_all=True, reparam=reparam)

        if return_all is False:
            output_dict = get_dict_values(x, self._var, return_dict=True)
            return output_dict

        return output_dict

    def get_log_prob(self, x, sum_features=True, feature_dims=None):
        parent_log_prob = self._parent.get_log_prob(x, sum_features=sum_features, feature_dims=feature_dims)
        child_log_prob = self._child.get_log_prob(x, sum_features=sum_features, feature_dims=feature_dims)

        if sum_features:
            return parent_log_prob + child_log_prob

        if parent_log_prob.size() == child_log_prob.size():
            return parent_log_prob + child_log_prob

        raise ValueError("Two PDFs, {} and {}, have different sizes,"
                         " so you must set sum_dim=True.".format(self._parent.prob_text, self._child.prob_text))

    def __repr__(self):
        return self._parent.__repr__() + "\n" + self._child.__repr__()


class ReplaceVarDistribution(Distribution):
    """Replace names of variables in Distribution.

    Examples
    --------
    >>> a = DistributionBase(var=["x"], cond_var=["z"])
    >>> print(a.prob_text)
    p(x|z)
    >>> replace_dict = {'x': 'y'}
    >>> p_repl = ReplaceVarDistribution(a, replace_dict)
    >>> print(p_repl.prob_text)
    p(y|z)

    """

    def __init__(self, a, replace_dict):
        """
        Parameters
        ----------
        a : pixyz.Distribution (not pixyz.MultiplyDistribution)
            Distribution.

        replace_dict : dict
            Dictionary.

        """
        if not isinstance(a, Distribution):
            raise ValueError("Given input should be `pixyz.Distribution`, got {}.".format(type(a)))

        if isinstance(a, MultiplyDistribution):
            raise ValueError("`pixyz.MultiplyDistribution` is not supported for now.")

        if isinstance(a, MarginalizeVarDistribution):
            raise ValueError("`pixyz.MarginalizeVarDistribution` is not supported for now.")

        _cond_var = deepcopy(a.cond_var)
        _var = deepcopy(a.var)
        all_vars = _cond_var + _var

        if not (set(replace_dict.keys()) <= set(all_vars)):
            raise ValueError

        _replace_inv_cond_var_dict = {replace_dict[var]: var for var in _cond_var if var in replace_dict.keys()}
        _replace_inv_dict = {value: key for key, value in replace_dict.items()}

        self._replace_inv_cond_var_dict = _replace_inv_cond_var_dict
        self._replace_inv_dict = _replace_inv_dict
        self._replace_dict = replace_dict

        _cond_var = [replace_dict[var] if var in replace_dict.keys() else var for var in _cond_var]
        _var = [replace_dict[var] if var in replace_dict.keys() else var for var in _var]
        super().__init__(cond_var=_cond_var, var=_var, name=a.name, dim=a.dim)

        self._a = a
        _input_var = [replace_dict[var] if var in replace_dict.keys() else var for var in a.input_var]
        self._input_var = _input_var

    def forward(self, *args, **kwargs):
        return self._a.forward(*args, **kwargs)

    def get_params(self, params_dict):
        params_dict = replace_dict_keys(params_dict, self._replace_inv_cond_var_dict)
        return self._a.get_params(params_dict)

    def set_dist(self, x={}, sampling=False, **kwargs):
        x = replace_dict_keys(x, self._replace_inv_cond_var_dict)
        return self._a.set_dist(x=x, sampling=sampling, **kwargs)

    def sample(self, x={}, shape=None, batch_size=1, return_all=True, reparam=False):
        input_dict = get_dict_values(x, self.cond_var, return_dict=True)
        replaced_input_dict = replace_dict_keys(input_dict, self._replace_inv_cond_var_dict)

        output_dict = self._a.sample(replaced_input_dict, shape=shape, batch_size=batch_size,
                                     return_all=False, reparam=reparam)
        output_dict = replace_dict_keys(output_dict, self._replace_dict)

        x.update(output_dict)
        return x

    def get_log_prob(self, x_dict, **kwargs):
        input_dict = get_dict_values(x_dict, self.cond_var + self.var, return_dict=True)
        input_dict = replace_dict_keys(input_dict, self._replace_inv_dict)
        return self._a.get_log_prob(input_dict, **kwargs)

    def sample_mean(self, x):
        input_dict = get_dict_values(x, self.cond_var, return_dict=True)
        input_dict = replace_dict_keys(input_dict, self._replace_inv_cond_var_dict)
        return self._a.sample_mean(input_dict)

    def sample_variance(self, x):
        input_dict = get_dict_values(x, self.cond_var, return_dict=True)
        input_dict = replace_dict_keys(input_dict, self._replace_inv_cond_var_dict)
        return self._a.sample_variance(input_dict)

    @property
    def input_var(self):
        return self._input_var

    @property
    def distribution_name(self):
        return self._a.distribution_name

    def __repr__(self):
        return self._a.__repr__()

    def __getattr__(self, item):
        try:
            return super().__getattr__(item)
        except AttributeError:
            return self._a.__getattribute__(item)


class MarginalizeVarDistribution(Distribution):
    r"""Marginalize variables in Distribution.

    .. math::
        p(x) = \int p(x,z) dz

    Examples
    --------
    >>> a = DistributionBase(var=["x"], cond_var=["z"])
    >>> b = DistributionBase(var=["y"], cond_var=["z"])
    >>> p_multi = a * b
    >>> print(p_multi.prob_text)
    p(x,y|z)
    >>> p_marg = MarginalizeVarDistribution(p_multi, ["y"])
    >>> print(p_marg.prob_text, p_marg.prob_factorized_text)
    p(x|z) \int p(x|z)p(y|z)dy

    """

    def __init__(self, a, marginalize_list):
        """
        Parameters
        ----------
        a : pixyz.Distribution (not pixyz.DistributionBase)
            Distribution.

        marginalize_list : list
            Variables to marginalize.

        """
        marginalize_list = tolist(marginalize_list)

        if not isinstance(a, Distribution):
            raise ValueError("Given input must be `pixyz.Distribution`, got {}.".format(type(a)))

        if isinstance(a, DistributionBase):
            raise ValueError("`pixyz.DistributionBase` cannot marginalize its variables for now.")

        _var = deepcopy(a.var)
        _cond_var = deepcopy(a.cond_var)

        if not((set(marginalize_list)) < set(_var)):
            raise ValueError()

        if not((set(marginalize_list)).isdisjoint(set(_cond_var))):
            raise ValueError()

        if len(marginalize_list) == 0:
            raise ValueError("Length of `marginalize_list` must be at least 1, got 0.")

        _var = [var for var in _var if var not in marginalize_list]

        super().__init__(cond_var=_cond_var, var=_var, name=a.name, dim=a.dim)
        self._a = a
        self._marginalize_list = marginalize_list

    def forward(self, *args, **kwargs):
        return self._a.forward(*args, **kwargs)

    def get_params(self, params_dict):
        return self._a.get_params(params_dict)

    def sample(self, x={}, shape=None, batch_size=1, return_all=True, reparam=False):
        output_dict = self._a.sample(x=x, shape=shape, batch_size=batch_size, return_all=False,
                                     reparam=reparam)
        output_dict = delete_dict_values(output_dict, self._marginalize_list)

        return output_dict

    def sample_mean(self, x):
        return self._a.sample_mean(x)

    def sample_variance(self, x):
        return self._a.sample_variance(x)

    @property
    def input_var(self):
        return self._a.input_var

    @property
    def distribution_name(self):
        return self._a.distribution_name

    @property
    def prob_factorized_text(self):
        integral_symbol = len(self._marginalize_list) * "\\int "
        integral_variables = ["d" + str(var) for var in self._marginalize_list]
        integral_variables = "".join(integral_variables)

        return "{}{}{}".format(integral_symbol, self._a.prob_factorized_text, integral_variables)

    def __repr__(self):
        return self._a.__repr__()

    def __getattr__(self, item):
        try:
            return super().__getattr__(item)
        except AttributeError:
            return self._a.__getattribute__(item)
