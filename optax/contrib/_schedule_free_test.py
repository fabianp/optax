# Copyright 2024 DeepMind Technologies Limited. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tests for `_schedule_free.py`."""

from absl.testing import absltest
from absl.testing import parameterized
import chex
import jax
import jax.numpy as jnp
import numpy as np
from optax._src import alias
from optax._src import numerics
from optax._src import schedule
from optax._src import update
from optax.contrib import _schedule_free
from optax.tree_utils import _state_utils


_WARM_LR = schedule.warmup_constant_schedule(0.0, 1e-2, 5_000)

# TODO(harshm): try other optimizers with schedule_free.
_OPTIMIZERS_UNDER_TEST = (
    dict(opt_name='sgd', opt_kwargs=dict(momentum=0.0)),
    dict(opt_name='adam', opt_kwargs=dict(b1=0.0)),
    dict(opt_name='adamw', opt_kwargs=dict(b1=0.0)),
)


def _setup_parabola(dtype):
  """Quadratic function as an optimization target."""
  initial_params = jnp.array([-1.0, 10.0, 1.0], dtype=dtype)
  final_params = jnp.array([1.0, -1.0, 1.0], dtype=dtype)

  @jax.grad
  def get_updates(params):
    return jnp.sum(numerics.abs_sq(params - final_params))

  return initial_params, final_params, get_updates


def _setup_rosenbrock(dtype):
  """Rosenbrock function as an optimization target."""
  a = 1.0
  b = 100.0

  initial_params = jnp.array([0.0, 0.0], dtype=dtype)
  final_params = jnp.array([a, a**2], dtype=dtype)

  @jax.grad
  def get_updates(params):
    return numerics.abs_sq(a - params[0]) + b * numerics.abs_sq(
        params[1] - params[0] ** 2
    )

  return initial_params, final_params, get_updates


class ScheduleFreeTest(chex.TestCase):

  def setUp(self):
    super().setUp()
    self.grads = {'x': np.array(2.0), 'y': np.array(-2.0)}
    self.initial_params = {'x': np.array(3.0), 'y': np.array(-3.0)}

  @parameterized.product(
      _OPTIMIZERS_UNDER_TEST,
      target=(_setup_parabola, _setup_rosenbrock),
      dtype=(jnp.float32,),
  )
  def test_optimization(self, opt_name, opt_kwargs, target, dtype):

    opt = getattr(alias, opt_name)(learning_rate=_WARM_LR, **opt_kwargs)
    opt = _schedule_free.schedule_free(opt, learning_rate=_WARM_LR)
    initial_params, final_params, get_updates = target(dtype)

    @jax.jit
    def step(params, state):
      updates = get_updates(params)
      updates, state = opt.update(updates, state, params)
      params = update.apply_updates(params, updates)
      return params, state

    params = initial_params
    state = opt.init(params)
    # A no-op change, to verify that tree map works.
    state = _state_utils.tree_map_params(opt, lambda v: v, state)

    for _ in range(25000):
      params, state = step(params, state)

    chex.assert_trees_all_close(
        _schedule_free.schedule_free_eval_params(state, params),
        final_params,
        rtol=3e-2,
        atol=3e-2,
    )

  @parameterized.parameters(*_OPTIMIZERS_UNDER_TEST)
  def test_learning_rate_zero(self, opt_name, opt_kwargs):
    opt = getattr(alias, opt_name)(learning_rate=0.0, **opt_kwargs)
    opt = _schedule_free.schedule_free(opt, learning_rate=0.0)
    initial_params, _, get_updates = _setup_parabola(jnp.float32)

    @jax.jit
    def step(params, state):
      updates = get_updates(params)
      updates, state = opt.update(updates, state, params)
      params = update.apply_updates(params, updates)
      return params, state

    params = initial_params
    state = opt.init(params)
    for _ in range(25000):
      params, state = step(params, state)

    chex.assert_trees_all_close(
        _schedule_free.schedule_free_eval_params(state, params),
        initial_params,
    )

  def test_schedule_free_adamw(self):
    def step(params, state, opt):
      updates = get_updates(params)
      updates, state = opt.update(updates, state, params)
      params = update.apply_updates(params, updates)
      return params, state

    initial_params, _, get_updates = _setup_parabola(jnp.float32)

    def run(opt):
      params = initial_params
      state = opt.init(params)

      for _ in range(5):
        params, state = step(params, state, opt)
      return params

    # Test with shortcut implementation
    opt_shortcut = _schedule_free.schedule_free_adamw(
        learning_rate=1.0,
        b1=0.9,
        weight_decay=1-4,
    )
    params_shortcut = run(opt_shortcut)

    # Test with wrapper implementation
    opt_wrapper = _schedule_free.schedule_free(
        alias.adamw(learning_rate=1.0, b1=0.0, weight_decay=1-4),
        learning_rate=1.0,
        b1=0.9,
    )
    params_wrapper = run(opt_wrapper)
    chex.assert_trees_all_close(params_shortcut, params_wrapper)

if __name__ == '__main__':
  absltest.main()
