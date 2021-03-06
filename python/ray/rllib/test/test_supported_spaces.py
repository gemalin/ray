import unittest
import traceback

import gym
from gym.spaces import Box, Discrete, Tuple, Dict
from gym.envs.registration import EnvSpec
import numpy as np
import sys

import ray
from ray.rllib.agents.agent import get_agent_class
from ray.rllib.test.test_multi_agent_env import MultiCartpole, MultiMountainCar
from ray.rllib.utils.error import UnsupportedSpaceException
from ray.tune.registry import register_env

ACTION_SPACES_TO_TEST = {
    "discrete": Discrete(5),
    "vector": Box(-1.0, 1.0, (5, ), dtype=np.float32),
    "tuple": Tuple(
        [Discrete(2),
         Discrete(3),
         Box(-1.0, 1.0, (5, ), dtype=np.float32)]),
}

OBSERVATION_SPACES_TO_TEST = {
    "discrete": Discrete(5),
    "vector": Box(-1.0, 1.0, (5, ), dtype=np.float32),
    "image": Box(-1.0, 1.0, (84, 84, 1), dtype=np.float32),
    "atari": Box(-1.0, 1.0, (210, 160, 3), dtype=np.float32),
    "tuple": Tuple([Discrete(10),
                    Box(-1.0, 1.0, (5, ), dtype=np.float32)]),
    "dict": Dict({
        "task": Discrete(10),
        "position": Box(-1.0, 1.0, (5, ), dtype=np.float32),
    }),
}


def make_stub_env(action_space, obs_space, check_action_bounds):
    class StubEnv(gym.Env):
        def __init__(self):
            self.action_space = action_space
            self.observation_space = obs_space
            self.spec = EnvSpec("StubEnv-v0")

        def reset(self):
            sample = self.observation_space.sample()
            return sample

        def step(self, action):
            if check_action_bounds and not self.action_space.contains(action):
                raise ValueError("Illegal action for {}: {}".format(
                    self.action_space, action))
            if (isinstance(self.action_space, Tuple)
                    and len(action) != len(self.action_space.spaces)):
                raise ValueError("Illegal action for {}: {}".format(
                    self.action_space, action))
            return self.observation_space.sample(), 1, True, {}

    return StubEnv


def check_support(alg, config, stats, check_bounds=False):
    for a_name, action_space in ACTION_SPACES_TO_TEST.items():
        for o_name, obs_space in OBSERVATION_SPACES_TO_TEST.items():
            print("=== Testing", alg, action_space, obs_space, "===")
            stub_env = make_stub_env(action_space, obs_space, check_bounds)
            register_env("stub_env", lambda c: stub_env())
            stat = "ok"
            a = None
            try:
                a = get_agent_class(alg)(config=config, env="stub_env")
                a.train()
            except UnsupportedSpaceException:
                stat = "unsupported"
            except Exception as e:
                stat = "ERROR"
                print(e)
                print(traceback.format_exc())
            finally:
                if a:
                    try:
                        a.stop()
                    except Exception as e:
                        print("Ignoring error stopping agent", e)
                        pass
            print(stat)
            print()
            stats[alg, a_name, o_name] = stat


def check_support_multiagent(alg, config):
    register_env("multi_mountaincar", lambda _: MultiMountainCar(2))
    register_env("multi_cartpole", lambda _: MultiCartpole(2))
    if alg == "DDPG":
        a = get_agent_class(alg)(config=config, env="multi_mountaincar")
    else:
        a = get_agent_class(alg)(config=config, env="multi_cartpole")
    try:
        a.train()
    finally:
        a.stop()


class ModelSupportedSpaces(unittest.TestCase):
    def setUp(self):
        ray.init(num_cpus=4)

    def tearDown(self):
        ray.shutdown()

    def testAll(self):
        stats = {}
        check_support("IMPALA", {"num_gpus": 0}, stats)
        check_support(
            "DDPG", {
                "noise_scale": 100.0,
                "timesteps_per_iteration": 1
            },
            stats,
            check_bounds=True)
        check_support("DQN", {"timesteps_per_iteration": 1}, stats)
        check_support(
            "A3C", {
                "num_workers": 1,
                "optimizer": {
                    "grads_per_step": 1
                }
            },
            stats,
            check_bounds=True)
        check_support(
            "PPO", {
                "num_workers": 1,
                "num_sgd_iter": 1,
                "train_batch_size": 10,
                "sample_batch_size": 10,
                "sgd_minibatch_size": 1,
            },
            stats,
            check_bounds=True)
        check_support(
            "ES", {
                "num_workers": 1,
                "noise_size": 10000000,
                "episodes_per_batch": 1,
                "train_batch_size": 1
            }, stats)
        check_support(
            "ARS", {
                "num_workers": 1,
                "noise_size": 10000000,
                "num_rollouts": 1,
                "rollouts_used": 1
            }, stats)
        check_support(
            "PG", {
                "num_workers": 1,
                "optimizer": {}
            },
            stats,
            check_bounds=True)
        num_unexpected_errors = 0
        for (alg, a_name, o_name), stat in sorted(stats.items()):
            if stat not in ["ok", "unsupported"]:
                num_unexpected_errors += 1
            print(alg, "action_space", a_name, "obs_space", o_name, "result",
                  stat)
        self.assertEqual(num_unexpected_errors, 0)

    def testMultiAgent(self):
        check_support_multiagent("IMPALA", {"num_gpus": 0})
        check_support_multiagent("DQN", {"timesteps_per_iteration": 1})
        check_support_multiagent("A3C", {
            "num_workers": 1,
            "optimizer": {
                "grads_per_step": 1
            }
        })
        check_support_multiagent(
            "PPO", {
                "num_workers": 1,
                "num_sgd_iter": 1,
                "train_batch_size": 10,
                "sample_batch_size": 10,
                "sgd_minibatch_size": 1,
                "simple_optimizer": True,
            })
        check_support_multiagent("PG", {"num_workers": 1, "optimizer": {}})
        check_support_multiagent("DDPG", {"timesteps_per_iteration": 1})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke":
        ACTION_SPACES_TO_TEST = {
            "discrete": Discrete(5),
        }
        OBSERVATION_SPACES_TO_TEST = {
            "vector": Box(0.0, 1.0, (5, ), dtype=np.float32),
            "atari": Box(0.0, 1.0, (210, 160, 3), dtype=np.float32),
        }
    unittest.main(verbosity=2)
