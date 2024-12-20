# %%
from __future__ import annotations

import numpy as np
from os import path

from numba import jit
import h5py

from autoarray import numba_util
import autolens as al

# %%
with h5py.File("w_tilde.h5", "r") as f:
    w_ref = f["w_tilde"][:]

# %% [markdown]
# ## Example
# 
# We now load an interferometer dataset and input the quantities which use the function above to make
# the w tilde matrix.
# 
# ### Mask
# 
# We define the 'real_space_mask' which defines the grid the image the strong lens is evaluated using.
# 
# Basically, the lens model is evaluated in real space and then mapped to Fourier Space via the NUFFT. This
# matrix therefore defines the dimensions of certain matrices which enter our likelihood function and calculation.

# %%
real_space_mask = al.Mask2D.circular(
    shape_native=(100, 100),
    pixel_scales=0.2,
    radius=3.0,
)

# %% [markdown]
# ### Interferometer Dataset
# 
# We load an example interferometer dataset which will be used to help us develop the likelihood function.

# %%
dataset_type = "sma"
dataset_path = path.join("..", "..", "packages", "dirac_rse_interferometer", "dataset", dataset_type)

dataset = al.Interferometer.from_fits(
    data_path=path.join(dataset_path, "data.fits"),
    noise_map_path=path.join(dataset_path, "noise_map.fits"),
    uv_wavelengths_path=path.join(dataset_path, "uv_wavelengths.fits"),
    real_space_mask=real_space_mask,
    transformer_class=al.TransformerDFT,
)

# %% [markdown]
# ### W Tilde
# 
# The code below calls the w_tilde function above to create the w_tilde matrix, which we need to speed up via JAX.
# 
# Note that we have to put `np.array()` in front of all objects.
# 
# This is because JAX arrays are typed (e.g. `Array2D`) and need special behaviour to ensure JAX worked on the JAX'ed array. This is what `np.array()` achieves.
# 
# The source code now requires these `np.array()`'s to be in place even though we're currently using numba.

# %%
from jax import jit
import jax.numpy as jnp

@jit
def w_tilde_data_interferometer_from(
    visibilities_real: np.ndarray[tuple[int], np.float64],
    noise_map_real: np.ndarray[tuple[int], np.float64],
    uv_wavelengths: np.ndarray[tuple[int, int], np.float64],
    grid_radians_slim: np.ndarray[tuple[int, int], np.float64],
    native_index_for_slim_index: np.ndarray[tuple[int, int], np.int64],
) -> np.ndarray[tuple[int], np.float64]:
    """
    The matrix w_tilde is a matrix of dimensions [image_pixels, image_pixels] that encodes the PSF convolution of
    every pair of image pixels given the noise map. This can be used to efficiently compute the curvature matrix via
    the mappings between image and source pixels, in a way that omits having to perform the PSF convolution on every
    individual source pixel. This provides a significant speed up for inversions of imaging datasets.

    When w_tilde is used to perform an inversion, the mapping matrices are not computed, meaning that they cannot be
    used to compute the data vector. This method creates the vector `w_tilde_data` which allows for the data
    vector to be computed efficiently without the mapping matrix.

    The matrix w_tilde_data is dimensions [image_pixels] and encodes the PSF convolution with the `weight_map`,
    where the weights are the image-pixel values divided by the noise-map values squared:

    weight = image / noise**2.0

    This function can almost be numba-jitted with
        @jit("float64[::1](float64[::1], float64[::1], float64[:,::1], float64[:,::1], int64[:,::1])", nopython=True, nogil=True, parallel=True)
    but numba doesn't like the combination of flip and reshape there.

    Parameters
    ----------
    visibilities_real : ndarray, shape (N,), dtype=float64
        The two dimensional masked image of values which `w_tilde_data` is computed from.
    noise_map_real : ndarray, shape (N,), dtype=float64
        The two dimensional masked noise-map of values which `w_tilde_data` is computed from.
    uv_wavelengths : ndarray, shape (N, 2), dtype=float64
        The UV wavelengths for the visibility calculations.
    grid_radians_slim : ndarray, shape (M, 2), dtype=float64
        The grid in radians in slim format.
    native_index_for_slim_index : ndarray, shape (M, 2), dtype=int64
        An array that maps pixels from the slimmed array to the native array.

    Returns
    -------
    ndarray, shape (M,), dtype=float64
        A matrix that encodes the PSF convolution values between the imaging divided by the noise map**2 that enables
        efficient calculation of the data vector.
    """
    return (
        # (1, N)
        jnp.square(jnp.square(noise_map_real) / visibilities_real).reshape(1, -1) *
        jnp.cos(
            (2.0 * jnp.pi) *
            # (M, N)
            (
                # (M, 1, 2)
                jnp.flip(grid_radians_slim.reshape(-1, 1, 2), 2) *
                # (1, N, 2)
                uv_wavelengths.reshape(1, -1, 2)
            ).sum(axis=2)
        )
    ).sum(axis=1)

# %%
w_tilde = w_tilde_data_interferometer_from(
    visibilities_real=np.array(dataset.data.real),
    noise_map_real=np.array(dataset.noise_map.real),
    uv_wavelengths=np.array(dataset.uv_wavelengths),
    grid_radians_slim=np.array(dataset.grid.in_radians),
    native_index_for_slim_index=np.array(real_space_mask.derive_indexes.native_for_slim).astype("int")
)

# %%
np.testing.assert_allclose(w_tilde, w_ref)


