"""
Morph an input dataset of 2D points into select shapes, while preserving the summary
statistics to a given number of decimal points through simulated annealing.

.. note::
    This code has been altered by Stefanie Molin to work for other input datasets
    by parameterizing the target shapes with information from the input shape.
    The original code works for a specific dataset called the "dinosaurus" and was created
    for the paper "Same Stats, Different Graphs: Generating Datasets with Varied Appearance and
    Identical Statistics through Simulated Annealing" by Justin Matejka and George Fitzmaurice
    (ACM CHI 2017).

    The paper, video, and associated code and datasets can be found on the
    Autodesk Research website `here <https://www.autodeskresearch.com/publications/samestats>`_.
"""

import os
from typing import Iterable, Optional, Union

import numpy as np
import pandas as pd
import pytweening
import tqdm

from .data.stats import get_values
from .plotting import plot, stitch_gif_animation
from .shapes.bases.shape import Shape


class DataMorpher:
    """Class for morphing a dataset into a target shape, preserving summary statistics."""

    def __init__(
        self,
        *,
        decimals: int,
        output_dir: str,
        in_notebook: bool,
        write_data: bool = False,
        seed: Optional[int] = None,
        num_frames: int = 100,
        keep_frames: bool = False,
        forward_only_animation: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.keep_frames = keep_frames
        self.write_data = write_data
        self.seed = seed
        self.forward_only_animation = forward_only_animation

        if not isinstance(decimals, int) or decimals < 0 or decimals > 5:
            raise ValueError(
                'decimals must be a non-negative integer less than or equal to 5.'
            )
        self.decimals = decimals

        if not isinstance(num_frames, int) or num_frames <= 0 or num_frames > 100:
            raise ValueError(
                'num_frames must be a positive integer less than or equal to 100.'
            )
        self.num_frames = num_frames

        self.looper = tqdm.tnrange if in_notebook else tqdm.trange

    def _select_frames(
        self, iterations: int, ramp_in: bool, ramp_out: bool, freeze_for: int
    ) -> list:
        """
        Identify the frames to capture for the animation.

        Parameters
        ----------
        iterations : int
            The number of iterations.
        ramp_in : bool
            Whether to more slowly transition in the beginning.
        ramp_out : bool
            Whether to slow down the transition at the end.
        freeze_for : int
            The number of frames to freeze at the end.

        Returns
        -------
        list
            The list of frame numbers to include in the animation.
        """
        if ramp_in and not ramp_out:
            easing_function = pytweening.easeInSine
        elif ramp_out and not ramp_in:
            easing_function = pytweening.easeOutSine
        elif ramp_out and ramp_in:
            easing_function = pytweening.easeInOutSine
        else:
            easing_function = pytweening.linear

        frames = [
            int(round(easing_function(x) * iterations))
            for x in np.arange(0, 1, 1 / (self.num_frames - freeze_for))
        ]

        extras = [iterations] * freeze_for
        frames.extend(extras)

        return frames

    def _is_close_enough(self, df1: pd.DataFrame, df2: pd.DataFrame) -> bool:
        """
        Check whether the statistics are within the acceptable bounds.

        Parameters
        ----------
        df1 : pd.DataFrame
            The original DataFrame.
        df2 : pd.DataFrame
            The DataFrame after the latest perturbation.

        Returns
        -------
        bool
            Whether the values are the same to :attr:`decimals`.
        """
        return np.all(
            np.abs(
                np.subtract(
                    *(
                        np.floor(np.array(get_values(data)) * 10**self.decimals)
                        for data in [df1, df2]
                    )
                )
            )
            == 0
        )

    @staticmethod
    def _perturb(
        df: pd.DataFrame,
        target: Shape,
        *,
        shake: float,
        allowed_dist: Union[int, float],
        temp: Union[int, float],
        x_bounds: Iterable[Union[int, float]],
        y_bounds: Iterable[Union[int, float]],
    ) -> pd.DataFrame:
        """
        Perform one round of perturbation.

        Parameters
        ----------
        df : pandas.DataFrame
            The data to perturb.
        target : Shape
            The shape to morph the data into.
        shake : float
            The maximum amount of movement in each direction.
        allowed_dist : int or float
            The farthest apart the perturbed points can be from the target shape.
        temp : int or float
            The temperature for simulated annealing. The higher the temperature
            the more we are willing to accept perturbations that might be worse than
            what we had before. The goal is to avoid local optima.
        x_bounds : Iterable[Union[int, float]]
            The minimum/maximum x values.
        y_bounds : Iterable[Union[int, float]]
            The minimum/maximum y values.

        Returns
        -------
        pandas.DataFrame
            The input dataset with one point perturbed.
        """
        row = np.random.randint(0, len(df))
        initial_x = df.at[row, 'x']
        initial_y = df.at[row, 'y']

        # this is the simulated annealing step, if "do_bad", then we are willing to
        # accept a new state which is worse than the current one
        do_bad = np.random.random_sample() < temp

        done = False
        while not done:
            new_x = initial_x + np.random.randn() * shake
            new_y = initial_y + np.random.randn() * shake

            old_dist = target.distance(initial_x, initial_y)
            new_dist = target.distance(new_x, new_y)

            close_enough = new_dist < old_dist or new_dist < allowed_dist or do_bad
            within_bounds = (
                new_y > y_bounds[0]
                and new_y < y_bounds[1]
                and new_x > x_bounds[0]
                and new_x < x_bounds[1]
            )
            done = close_enough and within_bounds

        df.loc[row, 'x'] = new_x
        df.loc[row, 'y'] = new_y

        return df

    def morph(
        self,
        start_shape_name: str,
        start_shape_data: pd.DataFrame,
        target: Shape,
        *,
        iterations: int = 100_000,
        max_temp: Union[int, float] = 0.4,
        min_temp: Union[int, float] = 0,
        shake: float = 0.3,
        allowed_dist: Union[int, float] = 2,
        ramp_in: bool = False,
        ramp_out: bool = False,
        freeze_for: int = 0,
    ) -> pd.DataFrame:
        """
        Morph a dataset into a target shape by perturbing it
        with simulated annealing.

        Notes
        -----
        This method saves data to disk to :attr:`output_dir`, which
        includes frames and/or animation and, depending on :attr:`write_data`,
        CSV files for each frame.

        Parameters
        ----------
        start_shape_name : str
            The name of the starting shape (for file naming).
        start_shape_data : pandas.DataFrame
            The data for the starting shape.
        target : Shape
            The shape we want to morph into.
        iterations : int
            The number of iterations to run simulated annealing for.
        max_temp : int or float
            The maximum temperature for simulated annealing (starting temperature).
        min_temp : int or float
            The minimum temperature for simulated annealing (ending temperature).
        shake : float
            The maximum amount of movement in each direction.
        allowed_dist : int or float
            The farthest apart the perturbed points can be from the target shape.
        ramp_in : bool, default False
            Whether to more slowly transition in the beginning.
        ramp_out : bool, default False
            Whether to slow down the transition at the end.
        freeze_for : int, default 0
            The number of frames to freeze at the end.

        Returns
        -------
        pandas.DataFrame
            The morphed data.
        """
        morphed_data = start_shape_data.copy()

        if self.seed is not None:
            np.random.seed(self.seed)

        # iteration numbers that we will end up writing to file as frames
        frame_numbers = self._select_frames(
            iterations=iterations,
            ramp_in=ramp_in,
            ramp_out=ramp_out,
            freeze_for=freeze_for,
        )

        frame_count = 1
        for i in self.looper(
            iterations, leave=True, ascii=True, desc=f'{target} pattern'
        ):
            current_temp = (max_temp - min_temp) * pytweening.easeInOutQuad(
                ((iterations - i) / iterations)
            ) + min_temp

            # TODO: derive these bounds based on the data? or just normalize the data to be within these to start?
            perturbed_data = self._perturb(
                morphed_data.copy(),
                target=target,
                x_bounds=[0, 100],
                y_bounds=[0, 100],
                shake=shake,
                allowed_dist=allowed_dist,
                temp=current_temp,
            )

            if self._is_close_enough(start_shape_data, perturbed_data):
                morphed_data = perturbed_data

            base_name = f'{start_shape_name}-to-{target}'
            for _ in range(frame_numbers.count(i)):
                plot(
                    morphed_data,
                    save_to=os.path.join(
                        self.output_dir, f'{base_name}-image-{frame_count:03d}.png'
                    ),
                    dpi=150,
                )
                if self.write_data:
                    morphed_data.to_csv(
                        os.path.join(
                            self.output_dir, f'{base_name}-data-{frame_count:03d}.csv'
                        )
                    )

                frame_count += 1

        stitch_gif_animation(
            self.output_dir,
            start_shape_name,
            target_shape=target,
            keep_frames=self.keep_frames,
            forward_only_animation=self.forward_only_animation,
        )
        return morphed_data
