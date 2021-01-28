import dataclasses

import datargs
import fannypack
import jax
import torch
from jax import numpy as jnp
from tqdm.auto import tqdm

import data
import jaxfg
import networks
from trainer import Trainer

# Setup configuration
fannypack.utils.pdb_safety_net()

# Define and parse arguments
@dataclasses.dataclass
class Args:
    experiment_name: str = datargs.arg(help="Experiment name.")


args: Args = datargs.parse(Args)

# Set up tensorboard
summary_writer = torch.utils.tensorboard.SummaryWriter(
    log_dir=f"logs/{args.experiment_name}"
)

# Prep for training
dataloader = torch.utils.data.DataLoader(
    data.KittiSingleStepDataset(train=True),
    batch_size=32,
    collate_fn=data.collate_fn,
)

# Create our network
model, optimizer = networks.make_observation_cnn(seed=0)

trainer = Trainer(experiment_name=args.experiment_name)
optimizer = trainer.load_checkpoint(optimizer)


# Define loss, gradients, etc
@jax.jit
def mse_loss(
    model_params: jaxfg.types.PyTree,
    batched_images: jnp.ndarray,
    batched_velocities: jnp.ndarray,
):
    pred_velocities = model.apply(model_params, batched_images)[..., :2]
    assert pred_velocities.shape == batched_velocities.shape
    return jnp.mean((pred_velocities - batched_velocities) ** 2)


@jax.jit
def get_standard_deviations(minibatch: data.KittiStructNormalized):
    return (
        jnp.std(model.apply(optimizer.target, minibatch.get_stacked_image()), axis=0),
        jnp.std(minibatch.get_stacked_velocity(), axis=0),
    )


loss_grad_fn = jax.jit(jax.value_and_grad(mse_loss, argnums=0))

num_epochs = 30
progress = tqdm(range(num_epochs))
losses = []
for epoch in progress:
    minibatch: data.KittiStructNormalized
    for i, minibatch in enumerate(dataloader):
        loss_value, grad = loss_grad_fn(
            optimizer.target,
            minibatch.get_stacked_image(),
            minibatch.get_stacked_velocity(),
        )
        optimizer = optimizer.apply_gradient(grad, learning_rate=1e-4)
        losses.append(loss_value)

        if optimizer.state.step < 10 or optimizer.state.step % 10 == 0:
            # Log to Tensorboard
            summary_writer.add_scalar(
                "train/loss", float(loss_value), global_step=optimizer.state.step
            )

        if optimizer.state.step % 100 == 0:
            trainer.metadata["loss"] = float(loss_value)
            trainer.save_checkpoint(optimizer)

        if optimizer.state.step % 500 == 0:
            standard_deviations = get_standard_deviations(minibatch)
            print(f"{optimizer.state.step} Standard deviations:", standard_deviations)
            summary_writer.add_scalar(
                "train/std_pred_linear",
                float(standard_deviations[0][0]),
                global_step=optimizer.state.step,
            )
            summary_writer.add_scalar(
                "train/std_pred_angular",
                float(standard_deviations[0][1]),
                global_step=optimizer.state.step,
            )

            summary_writer.add_scalar(
                "train/std_label_linear",
                float(standard_deviations[1][0]),
                global_step=optimizer.state.step,
            )
            summary_writer.add_scalar(
                "train/std_label_angular",
                float(standard_deviations[1][1]),
                global_step=optimizer.state.step,
            )


trainer.save_checkpoint(optimizer)