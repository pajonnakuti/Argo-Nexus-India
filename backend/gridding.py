"""
DIVA-style Gridding Engine for Argo Nexus India
================================================

Implements Optimal Interpolation (OI) and standard scipy gridding
to produce regular lat/lon gridded fields from scattered Argo profiles.
Mathematically equivalent to the DIVA (Data-Interpolating Variational Analysis)
approach but implemented in pure Python via scipy.

Supported methods:
  - 'oi'       : Optimal Interpolation with Gaussian correlation (DIVA-like)
  - 'linear'   : Delaunay-based linear interpolation
  - 'nearest'  : Nearest-neighbor interpolation
"""

import numpy as np
import xarray as xr
from scipy.interpolate import griddata, RBFInterpolator
from scipy.spatial.distance import cdist
from datetime import datetime


def optimal_interpolation(lons, lats, values, grid_lon, grid_lat,
                          corr_length=2.0, snr=1.0):
    """
    DIVA-style Optimal Interpolation using Gaussian correlation function.

    Parameters
    ----------
    lons, lats : 1-D arrays of observation positions (degrees)
    values     : 1-D array of observation values
    grid_lon, grid_lat : 2-D meshgrid arrays of output positions
    corr_length : correlation length in degrees (controls smoothing)
    snr         : signal-to-noise ratio (higher = trust data more)

    Returns
    -------
    (analysis, error) : tuple of 2-D numpy arrays
        analysis — interpolated field on the grid
        error    — DIVA error field (0 = perfect, 1 = no data influence)
    """
    obs_points = np.column_stack([lons, lats])
    grid_points = np.column_stack([grid_lon.ravel(), grid_lat.ravel()])

    n_obs = len(values)

    if n_obs == 0:
        return np.full(grid_lon.shape, np.nan), np.ones(grid_lon.shape)

    if n_obs > 5000:
        # For large datasets, fall back to RBF for performance
        return _rbf_interpolation(lons, lats, values, grid_lon, grid_lat, corr_length)

    # Build observation-observation distance matrix
    obs_dist = cdist(obs_points, obs_points, metric='euclidean')

    # Gaussian correlation matrix C(r) = exp(-r² / (2 * L²))
    C_obs = np.exp(-(obs_dist ** 2) / (2.0 * corr_length ** 2))

    # Add noise to diagonal: C + (1/SNR) * I
    noise_var = 1.0 / max(snr, 0.01)
    C_obs += noise_var * np.eye(n_obs)

    # Compute cross-correlation between grid points and observations
    grid_dist = cdist(grid_points, obs_points, metric='euclidean')
    C_grid = np.exp(-(grid_dist ** 2) / (2.0 * corr_length ** 2))

    # Solve for weights: analysis = C_grid @ inv(C_obs) @ values
    try:
        weights = np.linalg.solve(C_obs, values)
        analysis = C_grid @ weights
    except np.linalg.LinAlgError:
        weights, _, _, _ = np.linalg.lstsq(C_obs, values, rcond=None)
        analysis = C_grid @ weights

    # ── DIVA Error Field ──────────────────────────────────────────────
    # error² = 1 - diag(C_grid @ inv(C_obs) @ C_grid^T)
    # For each grid point g:  err(g) = 1 - c_g^T @ C_obs^{-1} @ c_g
    # We compute inv(C_obs) @ C_grid^T column-by-column
    try:
        W = np.linalg.solve(C_obs, C_grid.T)          # shape: (n_obs, n_grid)
        # diag(C_grid @ W) = sum of element-wise product along obs axis
        error_sq = 1.0 - np.sum(C_grid * W.T, axis=1)  # shape: (n_grid,)
    except np.linalg.LinAlgError:
        W, _, _, _ = np.linalg.lstsq(C_obs, C_grid.T, rcond=None)
        error_sq = 1.0 - np.sum(C_grid * W.T, axis=1)

    # Clamp to [0, 1] (numerical noise can push slightly outside)
    error_sq = np.clip(error_sq, 0.0, 1.0)
    error_field = np.sqrt(error_sq)

    return analysis.reshape(grid_lon.shape), error_field.reshape(grid_lon.shape)


def _rbf_interpolation(lons, lats, values, grid_lon, grid_lat, corr_length):
    """
    RBF-based interpolation for large datasets.
    Uses scipy.interpolate.RBFInterpolator for scalable performance.

    Returns
    -------
    (analysis, error) : tuple of 2-D numpy arrays
    """
    obs_points = np.column_stack([lons, lats])
    grid_points = np.column_stack([grid_lon.ravel(), grid_lat.ravel()])

    try:
        rbf = RBFInterpolator(
            obs_points, values,
            kernel='gaussian',
            epsilon=1.0 / max(corr_length, 0.1),
            smoothing=0.1
        )
        result = rbf(grid_points).reshape(grid_lon.shape)

        # Estimate error from distance to nearest observation
        error_field = _distance_based_error(obs_points, grid_points, corr_length)
        return result, error_field.reshape(grid_lon.shape)
    except Exception:
        # Ultimate fallback
        return fallback_griddata(lons, lats, values, grid_lon, grid_lat, 'linear')


def _distance_based_error(obs_points, grid_points, corr_length):
    """
    Estimate error field from distance to nearest observation.
    Uses the same Gaussian decay as OI: error ≈ exp(-d_min / (2*L)).
    Points far from any observation get error close to 1.
    """
    from scipy.spatial import cKDTree
    tree = cKDTree(obs_points)
    d_min, _ = tree.query(grid_points)
    # Normalise: error = 1 - exp(-d² / (2*L²))
    error = 1.0 - np.exp(-(d_min ** 2) / (2.0 * corr_length ** 2))
    return np.clip(error, 0.0, 1.0)


def fallback_griddata(lons, lats, values, grid_lon, grid_lat, method='linear'):
    """
    Standard scipy griddata interpolation.

    Parameters
    ----------
    method : 'linear', 'nearest', or 'cubic'

    Returns
    -------
    (analysis, error) : tuple of 2-D numpy arrays
    """
    obs_points = np.column_stack([lons, lats])
    grid_points = np.column_stack([grid_lon.ravel(), grid_lat.ravel()])
    result = griddata(obs_points, values, (grid_lon, grid_lat), method=method)

    # For non-OI methods, estimate error from data density
    error_field = _distance_based_error(obs_points, grid_points, corr_length=2.0)
    # Where griddata returned NaN (outside convex hull), set error to 1
    nan_mask = np.isnan(result)
    error_field = error_field.reshape(grid_lon.shape)
    error_field[nan_mask] = 1.0

    return result, error_field


def grid_argo_data(profiles_data, variable, bounds, depth_level=10.0,
                   depth_tolerance=50.0, resolution=0.5, method='oi',
                   corr_length=2.0, snr=1.0):
    """
    Main gridding function. Takes extracted profile data rows and produces
    a gridded xarray.Dataset.

    Parameters
    ----------
    profiles_data : list of dicts
        Each dict has keys like 'Latitude', 'Longitude', 'depth', 'TEMP', etc.
    variable : str
        Variable to grid, e.g. 'TEMP', 'PSAL', 'DOXY', 'CHLA'
    bounds : dict
        {'north': float, 'south': float, 'east': float, 'west': float}
    depth_level : float
        Target depth in dbar/meters
    depth_tolerance : float
        Accept data within ± this range of depth_level
    resolution : float
        Grid spacing in degrees (0.25, 0.5, 1.0)
    method : str
        'oi' (optimal interpolation), 'linear', 'nearest'
    corr_length : float
        Correlation length for OI in degrees
    snr : float
        Signal-to-noise ratio for OI

    Returns
    -------
    xarray.Dataset with gridded field, or None if insufficient data
    """
    # --- Extract observations at the target depth level ---
    lons = []
    lats = []
    vals = []

    var_key = variable.upper()
    # Also check ADJUSTED variant
    var_adjusted_key = f"{var_key}_ADJUSTED"

    for row in profiles_data:
        depth = row.get('depth', None)
        if depth is None or depth == '':
            continue

        try:
            depth_val = float(depth)
        except (ValueError, TypeError):
            continue

        # Check if within depth tolerance
        if abs(depth_val - depth_level) > depth_tolerance:
            continue

        # Get variable value (prefer adjusted)
        val = row.get(var_adjusted_key, row.get(var_key, None))
        if val is None or val == '' or val == 'nan':
            continue

        try:
            val_float = float(val)
        except (ValueError, TypeError):
            continue

        if np.isnan(val_float):
            continue

        lat = row.get('Latitude', None)
        lon = row.get('Longitude', None)
        if lat is None or lon is None:
            continue

        try:
            lats.append(float(lat))
            lons.append(float(lon))
            vals.append(val_float)
        except (ValueError, TypeError):
            continue

    if len(vals) < 3:
        return None  # Not enough data points for interpolation

    lons = np.array(lons)
    lats = np.array(lats)
    vals = np.array(vals)

    # Remove duplicate locations (average values at same position)
    coords = np.round(np.column_stack([lons, lats]), 4)
    unique_coords, inverse = np.unique(coords, axis=0, return_inverse=True)
    avg_vals = np.zeros(len(unique_coords))
    counts = np.zeros(len(unique_coords))
    for i, idx in enumerate(inverse):
        avg_vals[idx] += vals[i]
        counts[idx] += 1
    avg_vals /= counts
    lons = unique_coords[:, 0]
    lats = unique_coords[:, 1]
    vals = avg_vals

    # --- Build output grid ---
    grid_lons = np.arange(bounds['west'], bounds['east'] + resolution, resolution)
    grid_lats = np.arange(bounds['south'], bounds['north'] + resolution, resolution)
    grid_lon, grid_lat = np.meshgrid(grid_lons, grid_lats)

    # --- Interpolate ---
    if method == 'oi':
        gridded, error_field = optimal_interpolation(
            lons, lats, vals, grid_lon, grid_lat,
            corr_length=corr_length, snr=snr
        )
    elif method in ('linear', 'nearest', 'cubic'):
        gridded, error_field = fallback_griddata(lons, lats, vals, grid_lon, grid_lat, method)
    else:
        gridded, error_field = fallback_griddata(lons, lats, vals, grid_lon, grid_lat, 'linear')

    # --- Build xarray Dataset ---
    error_var = f'{variable}_error'
    ds = xr.Dataset(
        {
            variable: (['lat', 'lon'], gridded.astype(np.float32)),
            error_var: (['lat', 'lon'], error_field.astype(np.float32)),
        },
        coords={
            'lat': grid_lats,
            'lon': grid_lons,
        },
        attrs={
            'title': f'Argo Nexus DIVA Gridded {variable}',
            'source': 'Argo Nexus India — DIVA-style Optimal Interpolation',
            'institution': 'INCOIS / Argo Nexus',
            'references': 'Troupin et al. (2012), doi:10.1016/j.advwatres.2012.02.008',
            'variable': variable,
            'depth_level_dbar': depth_level,
            'depth_tolerance_dbar': depth_tolerance,
            'grid_resolution_deg': resolution,
            'interpolation_method': method,
            'correlation_length_deg': corr_length if method == 'oi' else 'N/A',
            'signal_to_noise_ratio': snr if method == 'oi' else 'N/A',
            'n_observations': len(vals),
            'bounds_north': bounds['north'],
            'bounds_south': bounds['south'],
            'bounds_east': bounds['east'],
            'bounds_west': bounds['west'],
            'created': datetime.utcnow().isoformat() + 'Z',
            'conventions': 'CF-1.8',
        }
    )

    # Add coordinate attributes
    ds['lat'].attrs = {'units': 'degrees_north', 'long_name': 'Latitude'}
    ds['lon'].attrs = {'units': 'degrees_east', 'long_name': 'Longitude'}
    ds[variable].attrs = {
        'long_name': _variable_long_names().get(variable, variable),
        'units': _variable_units().get(variable, 'unknown'),
    }
    ds[error_var].attrs = {
        'long_name': f'DIVA Interpolation Error for {_variable_long_names().get(variable, variable)}',
        'units': '1',
        'valid_min': 0.0,
        'valid_max': 1.0,
        'comment': '0 = data-rich (low error), 1 = no nearby observations (high error)',
    }

    return ds


def _variable_long_names():
    return {
        'TEMP': 'Sea Water Temperature',
        'PSAL': 'Practical Salinity',
        'DOXY': 'Dissolved Oxygen',
        'CHLA': 'Chlorophyll-a',
        'NITRATE': 'Nitrate',
        'PH': 'pH',
        'BBP700': 'Backscattering at 700nm',
        'PRES': 'Sea Water Pressure',
    }


def _variable_units():
    return {
        'TEMP': 'degree_Celsius',
        'PSAL': 'PSU',
        'DOXY': 'micromole/kg',
        'CHLA': 'mg/m3',
        'NITRATE': 'micromole/kg',
        'PH': '1',
        'BBP700': '1/m',
        'PRES': 'dbar',
    }
