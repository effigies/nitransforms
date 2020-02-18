# emacs: -*- mode: python-mode; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the NiBabel package for the
#   copyright and license terms.
#
### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Linear transforms."""
import warnings
import numpy as np
from pathlib import Path
from scipy import ndimage as ndi

from nibabel.loadsave import load as _nbload

from .base import (
    ImageGrid, TransformBase, SpatialReference,
    _as_homogeneous, EQUALITY_TOL
)
from . import io


class Affine(TransformBase):
    """Represents linear transforms on image data."""

    __slots__ = ('_matrix', )

    def __init__(self, matrix=None, reference=None):
        """
        Initialize a linear transform.

        Parameters
        ----------
        matrix : ndarray
            The coordinate transformation matrix **in physical
            coordinates**, mapping coordinates from *reference* space
            into *moving* space.
            This matrix should be provided in homogeneous coordinates.

        Examples
        --------
        >>> xfm = Affine(reference=datadir / 'someones_anatomy.nii.gz')
        >>> xfm.matrix  # doctest: +NORMALIZE_WHITESPACE
        array([[1., 0., 0., 0.],
               [0., 1., 0., 0.],
               [0., 0., 1., 0.],
               [0., 0., 0., 1.]])

        >>> xfm = Affine([[1, 0, 0, 4], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        >>> xfm.matrix  # doctest: +NORMALIZE_WHITESPACE
        array([[1, 0, 0, 4],
               [0, 1, 0, 0],
               [0, 0, 1, 0],
               [0, 0, 0, 1]])

        """
        super().__init__()
        if matrix is None:
            matrix = np.eye(4)

        self._matrix = np.array(matrix)
        if self._matrix.ndim != 2:
            raise TypeError('Affine should be 2D.')

        if self._matrix.shape[0] != self._matrix.shape[1]:
            raise TypeError('Matrix is not square.')

        if reference:
            self.reference = reference

    def __eq__(self, other):
        """
        Overload equals operator.

        Examples
        --------
        >>> xfm1 = Affine([[1, 0, 0, 4], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        >>> xfm2 = Affine(xfm1.matrix)
        >>> xfm1 == xfm2
        True

        """
        _eq = np.allclose(self.matrix, other.matrix, rtol=EQUALITY_TOL)
        if _eq and self._reference != other._reference:
            warnings.warn('Affines are equal, but references do not match.')
        return _eq

    @property
    def matrix(self):
        """Access the internal representation of this affine."""
        return self._matrix

    def map(self, x, inverse=False):
        r"""
        Apply :math:`y = f(x)`.

        Parameters
        ----------
        x : N x D numpy.ndarray
            Input RAS+ coordinates (i.e., physical coordinates).
        inverse : bool
            If ``True``, apply the inverse transform :math:`x = f^{-1}(y)`.

        Returns
        -------
        y : N x D numpy.ndarray
            Transformed (mapped) RAS+ coordinates (i.e., physical coordinates).

        Examples
        --------
        >>> xfm = Affine([[1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, 3], [0, 0, 0, 1]])
        >>> xfm.map((0,0,0))
        array([[1., 2., 3.]])

        >>> xfm.map((0,0,0), inverse=True)
        array([[-1., -2., -3.]])

        """
        affine = self._matrix
        coords = _as_homogeneous(x, dim=affine.shape[0] - 1).T
        if inverse is True:
            affine = np.linalg.inv(self._matrix)
        return affine.dot(coords).T[..., :-1]

    def _to_hdf5(self, x5_root):
        """Serialize this object into the x5 file format."""
        xform = x5_root.create_dataset('Transform', data=[self._matrix])
        xform.attrs['Type'] = 'affine'
        x5_root.create_dataset('Inverse', data=[np.linalg.inv(self._matrix)])

        if self._reference:
            self.reference._to_hdf5(x5_root.create_group('Reference'))

    def to_filename(self, filename, fmt='X5', moving=None):
        """Store the transform in BIDS-Transforms HDF5 file format (.x5)."""
        if fmt.lower() in ['itk', 'ants', 'elastix']:
            itkobj = io.itk.ITKLinearTransform.from_ras(self.matrix)
            itkobj.to_filename(filename)
            return filename

        # Rest of the formats peek into moving and reference image grids
        if moving is not None:
            moving = ImageGrid(moving)
        else:
            moving = self.reference

        if fmt.lower() == 'afni':
            afniobj = io.afni.AFNILinearTransform.from_ras(
                self.matrix, moving=moving, reference=self.reference)
            afniobj.to_filename(filename)
            return filename

        if fmt.lower() == 'fsl':
            fslobj = io.fsl.FSLLinearTransform.from_ras(
                self.matrix, moving=moving, reference=self.reference
            )
            fslobj.to_filename(filename)
            return filename

        if fmt.lower() == 'fs':
            # xform info
            lt = io.LinearTransform()
            lt['sigma'] = 1.
            lt['m_L'] = [self.matrix]
            # Just for reference, nitransforms does not write VOX2VOX
            lt['src'] = io.VolumeGeometry.from_image(moving)
            lt['dst'] = io.VolumeGeometry.from_image(self.reference)
            # to make LTA file format
            lta = io.LinearTransformArray()
            lta['type'] = 1  # RAS2RAS
            lta['xforms'].append(lt)

            with open(filename, 'w') as f:
                f.write(lta.to_string())
            return filename

        raise NotImplementedError

    @classmethod
    def from_filename(cls, filename, fmt='X5',
                      reference=None, moving=None):
        """Create an affine from a transform file."""
        if fmt.lower() in ('itk', 'ants', 'elastix'):
            _factory = io.itk.ITKLinearTransformArray
        elif fmt.lower() in ('lta', 'fs'):
            _factory = io.LinearTransformArray
        else:
            raise NotImplementedError

        struct = _factory.from_filename(filename)
        matrix = struct.to_ras(reference=reference, moving=moving)
        if cls == Affine:
            if np.shape(matrix)[0] != 1:
                raise TypeError(
                    'Cannot load transform array "%s"' % filename)
            matrix = matrix[0]
        return cls(matrix, reference=reference)


class LinearTransformsMapping(Affine):
    """Represents a series of linear transforms."""

    def __init__(self, transforms, reference=None):
        """
        Initialize a linear transform mapping.

        Parameters
        ----------
        transforms : :obj:`list`
            The inverse coordinate transformation matrix **in physical
            coordinates**, mapping coordinates from *reference* space
            into *moving* space.
            This matrix should be provided in homogeneous coordinates.

        Examples
        --------
        >>> xfm = LinearTransformsMapping([
        ...     [[1., 0, 0, 1.], [0, 1., 0, 2.], [0, 0, 1., 3.], [0, 0, 0, 1.]],
        ...     [[1., 0, 0, -1.], [0, 1., 0, -2.], [0, 0, 1., -3.], [0, 0, 0, 1.]],
        ... ])
        >>> xfm[0].matrix  # doctest: +NORMALIZE_WHITESPACE
        array([[1., 0., 0., 1.],
               [0., 1., 0., 2.],
               [0., 0., 1., 3.],
               [0., 0., 0., 1.]])

        """
        super().__init__(reference=reference)
        if (
            transforms is None
            or np.shape(transforms)[0] < 1
        ):
            raise TypeError(
                "Transforms should be a list of Affines or linear "
                "transforms matrix with at least two elements.")

        self._matrix = np.stack([
            (
                xfm if isinstance(xfm, Affine)
                else Affine(xfm)
            ).matrix
            for xfm in transforms
        ], axis=0)

    def __getitem__(self, i):
        """Enable indexed access to the series of matrices."""
        return Affine(self.matrix[i, ...], reference=self._reference)

    def __len__(self):
        """Enable using len()."""
        return len(self._matrix)

    def map(self, x, inverse=False):
        r"""
        Apply :math:`y = f(x)`.

        Parameters
        ----------
        x : N x D numpy.ndarray
            Input RAS+ coordinates (i.e., physical coordinates).
        inverse : bool
            If ``True``, apply the inverse transform :math:`x = f^{-1}(y)`.

        Returns
        -------
        y : N x D numpy.ndarray
            Transformed (mapped) RAS+ coordinates (i.e., physical coordinates).

        Examples
        --------
        >>> xfm = LinearTransformsMapping([
        ...     [[1., 0, 0, 1.], [0, 1., 0, 2.], [0, 0, 1., 3.], [0, 0, 0, 1.]],
        ...     [[1., 0, 0, -1.], [0, 1., 0, -2.], [0, 0, 1., -3.], [0, 0, 0, 1.]],
        ... ])
        >>> xfm.matrix
        array([[[ 1.,  0.,  0.,  1.],
                [ 0.,  1.,  0.,  2.],
                [ 0.,  0.,  1.,  3.],
                [ 0.,  0.,  0.,  1.]],
        <BLANKLINE>
               [[ 1.,  0.,  0., -1.],
                [ 0.,  1.,  0., -2.],
                [ 0.,  0.,  1., -3.],
                [ 0.,  0.,  0.,  1.]]])

        >>> y = xfm.map([(0, 0, 0), (-1, -1, -1), (1, 1, 1)])
        >>> y[0, :, :3]
        array([[1., 2., 3.],
               [0., 1., 2.],
               [2., 3., 4.]])

        >>> y = xfm.map([(0, 0, 0), (-1, -1, -1), (1, 1, 1)], inverse=True)
        >>> y[0, :, :3]
        array([[-1., -2., -3.],
               [-2., -3., -4.],
               [ 0., -1., -2.]])


        """
        affine = self.matrix
        coords = _as_homogeneous(x, dim=affine.shape[-1] - 1).T
        if inverse is True:
            affine = np.linalg.inv(affine)
        return np.swapaxes(affine.dot(coords), 1, 2)

    def to_filename(self, filename, fmt='X5', moving=None):
        """Store the transform in BIDS-Transforms HDF5 file format (.x5)."""
        if fmt.lower() in ['itk', 'ants', 'elastix']:
            itkobj = io.itk.ITKLinearTransformArray.from_ras(self.matrix)
            itkobj.to_filename(filename)
            return filename

        # Rest of the formats peek into moving and reference image grids
        if moving is not None:
            moving = ImageGrid(moving)
        else:
            moving = self.reference

        if fmt.lower() == 'afni':
            afniobj = io.afni.AFNILinearTransformArray.from_ras(
                self.matrix, moving=moving, reference=self.reference)
            afniobj.to_filename(filename)
            return filename

        if fmt.lower() == 'fsl':
            fslobj = io.fsl.FSLLinearTransformArray.from_ras(
                self.matrix, moving=moving, reference=self.reference
            )
            fslobj.to_filename(filename)
            return filename

        if fmt.lower() == 'fs':
            # xform info
            lt = io.LinearTransform()
            lt['sigma'] = 1.
            lt['m_L'] = self.matrix
            # Just for reference, nitransforms does not write VOX2VOX
            lt['src'] = io.VolumeGeometry.from_image(moving)
            lt['dst'] = io.VolumeGeometry.from_image(self.reference)
            # to make LTA file format
            lta = io.LinearTransformArray()
            lta['type'] = 1  # RAS2RAS
            lta['xforms'].append(lt)

            with open(filename, 'w') as f:
                f.write(lta.to_string())
            return filename

        raise NotImplementedError

    def apply(self, spatialimage, reference=None,
              order=3, mode='constant', cval=0.0, prefilter=True, output_dtype=None):
        """
        Apply a transformation to an image, resampling on the reference spatial object.

        Parameters
        ----------
        spatialimage : `spatialimage`
            The image object containing the data to be resampled in reference
            space
        reference : spatial object, optional
            The image, surface, or combination thereof containing the coordinates
            of samples that will be sampled.
        order : int, optional
            The order of the spline interpolation, default is 3.
            The order has to be in the range 0-5.
        mode : {'constant', 'reflect', 'nearest', 'mirror', 'wrap'}, optional
            Determines how the input image is extended when the resamplings overflows
            a border. Default is 'constant'.
        cval : float, optional
            Constant value for ``mode='constant'``. Default is 0.0.
        prefilter: bool, optional
            Determines if the image's data array is prefiltered with
            a spline filter before interpolation. The default is ``True``,
            which will create a temporary *float64* array of filtered values
            if *order > 1*. If setting this to ``False``, the output will be
            slightly blurred if *order > 1*, unless the input is prefiltered,
            i.e. it is the result of calling the spline filter on the original
            input.

        Returns
        -------
        resampled : `spatialimage` or ndarray
            The data imaged after resampling to reference space.

        """
        if reference is not None and isinstance(reference, (str, Path)):
            reference = _nbload(str(reference))

        _ref = self.reference if reference is None \
            else SpatialReference.factory(reference)

        if isinstance(spatialimage, (str, Path)):
            spatialimage = _nbload(str(spatialimage))

        data = np.squeeze(np.asanyarray(spatialimage.dataobj))
        output_dtype = output_dtype or data.dtype

        ycoords = self.map(_ref.ndcoords.T)
        targets = ImageGrid(spatialimage).index(  # data should be an image
            _as_homogeneous(np.vstack(ycoords), dim=_ref.ndim))

        if data.ndim == 4:
            if len(self) != data.shape[-1]:
                raise ValueError(
                    "Attempting to apply %d transforms on a file with "
                    "%d timepoints" % (len(self), data.shape[-1])
                )
            targets = targets.reshape((len(self), -1, targets.shape[-1]))
            resampled = np.stack([
                ndi.map_coordinates(
                    data[..., t],
                    targets[t, ..., :_ref.ndim].T,
                    output=output_dtype,
                    order=order,
                    mode=mode,
                    cval=cval,
                    prefilter=prefilter)
                for t in range(data.shape[-1])],
                axis=0
            )
        elif data.ndim in (2, 3):
            resampled = ndi.map_coordinates(
                data,
                targets[..., :_ref.ndim].T,
                output=output_dtype,
                order=order,
                mode=mode,
                cval=cval,
                prefilter=prefilter,
            )

        if isinstance(_ref, ImageGrid):  # If reference is grid, reshape
            newdata = resampled.reshape((len(self), *_ref.shape))
            moved = spatialimage.__class__(
                np.moveaxis(newdata, 0, -1),
                _ref.affine, spatialimage.header)
            moved.header.set_data_dtype(output_dtype)
            return moved

        return resampled


def load(filename, fmt='X5', reference=None, moving=None):
    """
    Load a linear transform file.

    Examples
    --------
    >>> xfm = load(datadir / 'affine-LAS.itk.tfm', fmt='itk')
    >>> isinstance(xfm, Affine)
    True

    >>> xfm = load(datadir / 'itktflist.tfm', fmt='itk')
    >>> isinstance(xfm, LinearTransformsMapping)
    True

    """
    xfm = LinearTransformsMapping.from_filename(
        filename, fmt=fmt, reference=reference, moving=moving
    )
    if len(xfm) == 1:
        return xfm[0]
    return xfm
