import abc
import dataclasses
import types
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Hashable,
    NamedTuple,
    Set,
    Tuple,
    Type,
    TypeVar,
)

import jax
import numpy as onp
from jax import numpy as jnp
from overrides import overrides

from . import _types, _utils

if TYPE_CHECKING:
    from . import AbstractRealVectorVariable, VariableBase


FactorType = TypeVar("FactorType", bound="FactorBase")


@_utils.immutable_dataclass
class FactorBase(abc.ABC):
    variables: Tuple["VariableBase"]
    """Variables connected to this factor. Immutable. (currently assumed but unenforced)"""

    scale_tril_inv: _types.ScaleTrilInv
    """Inverse square root of covariance matrix."""

    _static_fields: Set[str] = dataclasses.field(default=frozenset(), init=False)
    """Fields to ignore when stacking."""

    @property
    def error_dim(self) -> int:
        """Error dimensionality."""
        return self.scale_tril_inv.shape[0]

    def __init_subclass__(cls, **kwargs):
        """Register all factors as PyTree nodes."""
        super().__init_subclass__(**kwargs)
        jax.tree_util.register_pytree_node(
            cls, flatten_func=cls.flatten, unflatten_func=cls.unflatten
        )

    @classmethod
    def flatten(
        cls: Type[FactorType], v: FactorType
    ) -> Tuple[Tuple[jnp.ndarray], Tuple]:
        """Flatten a factor for use as a PyTree/parameter stacking."""
        v_dict = dataclasses.asdict(v)
        v_dict.pop("variables")
        v_dict.pop("_static_fields")

        array_data = {k: v for k, v in v_dict.items() if k not in cls._static_fields}
        aux_dict = {k: v for k, v in v_dict.items() if k not in array_data}

        return (
            tuple(array_data.values()),
            tuple(array_data.keys())
            + tuple(aux_dict.keys())
            + tuple(aux_dict.values()),
        )

    # _FactorAuxData(
    #     array_keys=tuple(v_dict.keys()), aux_dict=aux_dict
    # )

    @classmethod
    def unflatten(
        cls: Type[FactorType], treedef: Tuple, children: Tuple[jnp.ndarray]
    ) -> FactorType:
        """Unflatten a factor for use as a PyTree/parameter stacking."""
        array_keys = treedef[: len(children)]
        aux = treedef[len(children) :]
        aux_keys = aux[: len(aux) // 2]
        aux_values = aux[len(aux) // 2 :]

        return cls(
            variables=tuple(),
            **dict(zip(array_keys, children)),
            **dict(zip(aux_keys, aux_values)),
            # **aux_data.aux_dict
        )

    def group_key(self) -> _types.GroupKey:
        """Get unique key for grouping factors.

        Args:

        Returns:
            _types.GroupKey:
        """
        v: "VariableBase"
        return _types.GroupKey(
            factor_type=self.__class__,
            secondary_key=(
                tuple((type(v), v.get_parameter_dim()) for v in self.variables),
                self.error_dim,
            ),
        )

    @abc.abstractmethod
    def compute_error(self, *args: jnp.ndarray):
        """compute_error.

        Args:
            *args (jnp.ndarray): Arguments
        """


@_utils.immutable_dataclass
class LinearFactor(FactorBase):
    """Linearized factor, corresponding to the simple residual:
    $$
    r = ( \Sum_i A_i x_i ) - b_i
    $$
    """

    A_matrices: Tuple[onp.ndarray]
    b: onp.ndarray
    scale_tril_inv: onp.ndarray

    @overrides
    def compute_error(self, *variable_values: jnp.ndarray):
        linear_component = jnp.zeros_like(self.b)
        for A_matrix, value in zip(self.A_matrices, variable_values):
            linear_component = linear_component + A_matrix @ value
        return linear_component - self.b

    # @classmethod
    # def linearize_from_factor(
    #     cls, factor: FactorBase, assignments: _types.VariableAssignments
    # ) -> "LinearFactor":
    #     """Produce a LinearFactor object by linearizing an arbitrary factor.
    #
    #     Args:
    #         factor (FactorBase): Factor to linearize.
    #         assignments (_types.VariableAssignments): Assignments to linearize around.
    #
    #     Returns:
    #         LinearFactor: Linearized factor.
    #     """
    #     A_from_variable: Dict[
    #         "RealVectorVariable", Callable[[jnp.ndarray], jnp.ndarray]
    #     ] = {}
    #
    #     # Pull out only the assignments that we care about
    #     assignments = {variable: assignments[variable] for variable in factor.variables}
    #
    #     for variable in factor.variables:
    #
    #         def f(local_delta: jnp.ndarray) -> jnp.ndarray:
    #             # Make copy of assignments with updated variable value
    #             assignments_copy = assignments.copy()
    #             assignments_copy[variable] = variable.add_local(
    #                 x=assignments_copy[variable], local_delta=local_delta
    #             )
    #
    #             # Return whitened error
    #             return factor.scale_tril_inv @ factor.compute_error(*())
    #
    #         # Linearize around variable
    #         f_jvp = jax.jacfwd(f)(jnp.zeros(variable.get_local_parameter_dim()))[1]
    #         A_from_variable[variable.local_delta_variable] = f_jvp
    #
    #     error = factor.compute_error(assignments=assignments)
    #     return LinearFactor(
    #         A_from_variable=A_from_variable, b=-factor.scale_tril_inv @ error
    #     )


@_utils.immutable_dataclass
class PriorFactor(FactorBase):
    mu: jnp.ndarray
    variable_type: Type["VariableBase"]
    _static_fields = frozenset({"variable_type"})

    @staticmethod
    def make(
        variable: "VariableBase",
        mu: jnp.ndarray,
        scale_tril_inv: _types.ScaleTrilInv,
    ):
        print(variable)
        return PriorFactor(
            variables=(variable,),
            mu=mu,
            scale_tril_inv=scale_tril_inv,
            variable_type=type(variable),
        )

    @overrides
    def compute_error(self, variable_value: jnp.ndarray):
        return self.variable_type.subtract_local(self.mu, variable_value)


class _BeforeAfterTuple(NamedTuple):
    before: "VariableBase"
    after: "VariableBase"


@_utils.immutable_dataclass
class BetweenFactor(FactorBase):
    variables: _BeforeAfterTuple
    delta: jnp.ndarray
    variable_type: Type["VariableBase"]
    _static_fields = frozenset({"variable_type"})

    @staticmethod
    def make(
        before: "VariableBase",
        after: "VariableBase",
        delta: jnp.ndarray,
        scale_tril_inv: _types.ScaleTrilInv,
    ):
        assert type(before) == type(after)
        return BetweenFactor(
            variables=_BeforeAfterTuple(before=before, after=after),
            delta=delta,
            scale_tril_inv=scale_tril_inv,
            variable_type=type(before),
        )

    @overrides
    def compute_error(self, before_value: jnp.ndarray, after_value: jnp.ndarray):
        # return self.variable_type.subtract_local(before_value, after_value) - self.delta

        # const T diff = traits<T>::Compose(traits<T>::Inverse(v1), v2);
        # // manifold equivalent of x-z -> Local(z,x)
        # return traits<T>::Local(diff_, diff);

        return self.variable_type.subtract_local(
            after_value,
            self.variable_type.add_local(x=before_value, local_delta=self.delta),
        )