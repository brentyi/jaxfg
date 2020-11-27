import time

# import numpy as jnp
import jax
import jax.numpy as jnp


def f(carry, x):
    carry = carry + 1
    y = x
    return carry, y


init = 0  # initial carry value
xs = {"x": jnp.zeros(5), "y": jnp.zeros(4)}

print(jax.lax.scan(f, init, xs, unroll=0))
#