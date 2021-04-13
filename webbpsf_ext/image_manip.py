import numpy as np
import multiprocessing as mp
import six

import scipy
from scipy import fftpack
from scipy.ndimage import fourier_shift
from scipy.ndimage.interpolation import rotate

from astropy.convolution import convolve, convolve_fft
from astropy.io import fits

from poppy.utils import krebin

from .utils import S

# Program bar
from tqdm.auto import trange, tqdm

import logging
_log = logging.getLogger('webbpsf_ext')

###########################################################################
#    Image manipulation
###########################################################################

def pad_or_cut_to_size(array, new_shape, fill_val=0.0, offset_vals=None):
    """
    Resize an array to a new shape by either padding with zeros
    or trimming off rows and/or columns. The output shape can
    be of any arbitrary amount.

    Parameters
    ----------
    array : ndarray
        A 1D, 2D, or 3D array. If 3D, then taken to be a stack of images
        that are cropped or expanded in the same fashion.
    new_shape : tuple
        Desired size for the output array. For 2D case, if a single value, 
        then will create a 2-element tuple of the same value.
    fill_val : scalar, optional
        Value to pad borders. Default is 0.0
    offset_vals : tuple
        Option to perform image shift in the (xpix) direction for 1D, 
        or (ypix,xpix) direction for 2D/3D.

    Returns
    -------
    output : ndarray
        An array of size new_shape that preserves the central information 
        of the input array.
    """
    
    ndim = len(array.shape)
    if ndim == 1:
        # is_1d = True
        # Reshape array to a 2D array with nx=1
        array = array.reshape((1,1,-1))
        nz, ny, nx = array.shape
        if isinstance(new_shape, (float,int,np.int,np.int64)):
            nx_new = int(new_shape+0.5)
            ny_new = 1
            new_shape = (ny_new, nx_new)
        elif len(new_shape) < 2:
            nx_new = new_shape[0]
            ny_new = 1
            new_shape = (ny_new, nx_new)
        else:
            ny_new, nx_new = new_shape
        output = np.zeros(shape=(nz,ny_new,nx_new), dtype=array.dtype)
    elif (ndim == 2) or (ndim == 3):
        if ndim==2:
            nz = 1
            ny, nx = array.shape
            array = array.reshape([nz,ny,nx])
        else:
            nz, ny, nx = array.shape

        if isinstance(new_shape, (float,int,np.int,np.int64)):
            ny_new = nx_new = int(new_shape+0.5)
            new_shape = (ny_new, nx_new)
        elif len(new_shape) < 2:
            ny_new = nx_new = new_shape[0]
            new_shape = (ny_new, nx_new)
        else:
            ny_new, nx_new = new_shape
        output = np.zeros(shape=(nz,ny_new,nx_new), dtype=array.dtype)
    else:
        raise ValueError('Input image can only have 1 or 2 or 3 dimensions. \
                          Found {} dimensions.'.format(ndim))
                      
    # Return if no difference in shapes
    # This needs to occur after the above so that new_shape is verified to be a tuple
    # If offset_vals is set, then continue to perform shift function
    if (array.shape == new_shape) and (offset_vals is None):
        return array

    # Input the fill values
    if fill_val != 0:
        output += fill_val
        
    # Pixel shift values
    if offset_vals is not None:
        if ndim == 1:
            ny_off = 0
            if isinstance(offset_vals, (float,int,np.int,np.int64)):
                nx_off = offset_vals
            elif len(offset_vals) < 2:
                nx_off = offset_vals[0]
            else:
                raise ValueError('offset_vals should be a single value.')
        else:
            if len(offset_vals) == 2:
                ny_off, nx_off = offset_vals
            else:
                raise ValueError('offset_vals should have two values.')
    else:
        nx_off = ny_off = 0
                
    if nx_new>nx:
        n0 = (nx_new - nx) / 2
        n1 = n0 + nx
    elif nx>nx_new:
        n0 = (nx - nx_new) / 2
        n1 = n0 + nx_new
    else:
        n0, n1 = (0, nx)
    n0 = int(n0+0.5)
    n1 = int(n1+0.5)

    if ny_new>ny:
        m0 = (ny_new - ny) / 2
        m1 = m0 + ny
    elif ny>ny_new:
        m0 = (ny - ny_new) / 2
        m1 = m0 + ny_new
    else:
        m0, m1 = (0, ny)
    m0 = int(m0+0.5)
    m1 = int(m1+0.5)

    if (nx_new>=nx) and (ny_new>=ny):
        #print('Case 1')
        output[:,m0:m1,n0:n1] = array.copy()
        for i, im in enumerate(output):
            output[i] = fshift(im, delx=nx_off, dely=ny_off, pad=True, cval=fill_val)
    elif (nx_new<=nx) and (ny_new<=ny):
        #print('Case 2')
        if (nx_off!=0) or (ny_off!=0):
            array_temp = array.copy()
            for i, im in enumerate(array_temp):
                array_temp[i] = fshift(im, delx=nx_off, dely=ny_off, pad=True, cval=fill_val)
            output = array_temp[:,m0:m1,n0:n1]
        else:
            output = array[:,m0:m1,n0:n1]
    elif (nx_new<=nx) and (ny_new>=ny):
        #print('Case 3')
        if nx_off!=0:
            array_temp = array.copy()
            for i, im in enumerate(array_temp):
                array_temp[i] = fshift(im, delx=nx_off, pad=True, cval=fill_val)
            output[:,m0:m1,:] = array_temp[:,:,n0:n1]
        else:
            output[:,m0:m1,:] = array[:,:,n0:n1]
        for i, im in enumerate(output):
            output[i] = fshift(im, dely=ny_off, pad=True, cval=fill_val)
    elif (nx_new>=nx) and (ny_new<=ny):
        #print('Case 4')
        if ny_off!=0:
            array_temp = array.copy()
            for i, im in enumerate(array_temp):
                array_temp[i] = fshift(im, dely=ny_off, pad=True, cval=fill_val)
            output[:,:,n0:n1] = array_temp[:,m0:m1,:]
        else:
            output[:,:,n0:n1] = array[:,m0:m1,:]
        for i, im in enumerate(output):
            output[i] = fshift(im, delx=nx_off, pad=True, cval=fill_val)
        
    # Flatten if input and output arrays are 1D
    if (ndim==1) and (ny_new==1):
        output = output.flatten()
    elif ndim==2:
        output = output[0]

    return output

def fshift(image, delx=0, dely=0, pad=False, cval=0.0):
    """ Fractional image shift
    
    Ported from IDL function fshift.pro.
    Routine to shift an image by non-integer values.

    Parameters
    ----------
    image: ndarray
        1D or 2D array to be shifted
    delx : float
        shift in x (same direction as IDL SHIFT function)
    dely: float
        shift in y
    pad : bool
        Should we pad the array before shifting, then truncate?
        Otherwise, the image is wrapped.
    cval : sequence or float, optional
        The values to set the padded values for each axis. Default is 0.
        ((before_1, after_1), ... (before_N, after_N)) unique pad constants for each axis.
        ((before, after),) yields same before and after constants for each axis.
        (constant,) or int is a shortcut for before = after = constant for all axes.

        
    Returns
    -------
    ndarray
        Shifted image
    """
    
    
    if len(image.shape) == 1:
        # Return if delx is 0
        if np.isclose(delx, 0, atol=1e-5):
            return image

        # separate shift into an integer and fraction shift
        intx = np.int(delx)
        fracx = delx - intx
        if fracx < 0:
            fracx += 1
            intx -= 1

        # Pad ends with constant value
        if pad:
            padx = np.abs(intx) + 1
            out = np.pad(image,np.abs(intx),'constant',constant_values=cval)
        else:
            padx = 0
            out = image.copy()

        # shift by integer portion
        out = np.roll(out, intx)
        # if significant fractional shift...
        if not np.isclose(fracx, 0, atol=1e-5):
            out = out * (1.-fracx) + np.roll(out,1) * fracx

        out = out[padx:padx+image.size]
        return out

    elif len(image.shape) == 2:	
        # Return if both delx and dely are 0
        if np.isclose(delx, 0, atol=1e-5) and np.isclose(dely, 0, atol=1e-5):
            return image

        # separate shift into an integer and fraction shift
        intx = np.int(delx)
        inty = np.int(dely)
        fracx = delx - intx
        fracy = dely - inty
        if fracx < 0:
            fracx += 1
            intx -= 1
        if fracy < 0:
            fracy += 1
            inty -= 1

        # Pad ends with constant value
        if pad:
            padx = np.abs(intx) + 1
            pady = np.abs(inty) + 1
            pad_vals = ([pady]*2,[padx]*2)
            out = np.pad(image,pad_vals,'constant',constant_values=cval)
        else:
            padx = 0; pady = 0
            out = image.copy()

        # shift by integer portion
        out = np.roll(np.roll(out, intx, axis=1), inty, axis=0)
    
        # Check if fracx and fracy are effectively 0
        fxis0 = np.isclose(fracx,0, atol=1e-5)
        fyis0 = np.isclose(fracy,0, atol=1e-5)
        # If fractional shifts are significant
        # use bi-linear interpolation between four pixels
        if not (fxis0 and fyis0):
            # Break bi-linear interpolation into four parts
            # to avoid NaNs unnecessarily affecting integer shifted dimensions
            part1 = out * ((1-fracx)*(1-fracy))
            part2 = 0 if fyis0 else np.roll(out,1,axis=0)*((1-fracx)*fracy)
            part3 = 0 if fxis0 else np.roll(out,1,axis=1)*((1-fracy)*fracx)
            part4 = 0 if (fxis0 or fyis0) else np.roll(np.roll(out, 1, axis=1), 1, axis=0) * fracx*fracy
    
            out = part1 + part2 + part3 + part4
    
        out = out[pady:pady+image.shape[0], padx:padx+image.shape[1]]
        return out
            

        #if not np.allclose([fracx,fracy], 0, atol=1e-5):
        #	x = x * ((1-fracx)*(1-fracy)) + \
        #		np.roll(x,1,axis=0) * ((1-fracx)*fracy) + \
        #		np.roll(x,1,axis=1) * (fracx*(1-fracy)) + \
        #		np.roll(np.roll(x, 1, axis=1), 1, axis=0) * fracx*fracy

        #x = x[pady:pady+image.shape[0], padx:padx+image.shape[1]]
        #return x

    else:
        ndim = len(image.shape)
        raise ValueError(f'Input image can only have 1 or 2 dimensions. Found {ndim} dimensions.')

                          
def fourier_imshift(image, xshift, yshift, pad=False, cval=0.0):
    """Fourier shift image
    
    Shift an image by use of Fourier shift theorem

    Parameters
    ----------
    image : nd array
        N x K image
    xshift : float
        Number of pixels to shift image in the x direction
    yshift : float
        Number of pixels to shift image in the y direction
    pad : bool
        Should we pad the array before shifting, then truncate?
        Otherwise, the image is wrapped.
    cval : sequence or float, optional
        The values to set the padded values for each axis. Default is 0.
        ((before_1, after_1), ... (before_N, after_N)) unique pad constants for each axis.
        ((before, after),) yields same before and after constants for each axis.
        (constant,) or int is a shortcut for before = after = constant for all axes.

    Returns
    -------
    ndarray
        Shifted image
    """
    
    # Pad ends with zeros
    if pad:
        padx = np.abs(np.int(xshift)) + 1
        pady = np.abs(np.int(yshift)) + 1
        pad_vals = ([pady]*2,[padx]*2)
        im = np.pad(image,pad_vals,'constant',constant_values=cval)
    else:
        padx = 0; pady = 0
        im = image
    
    offset = fourier_shift( np.fft.fft2(im), (yshift,xshift) )
    offset = np.fft.ifft2(offset).real
    
    offset = offset[pady:pady+image.shape[0], padx:padx+image.shape[1]]
    
    return offset
    


def rotate_offset(data, angle, cen=None, cval=0.0, order=1, 
    reshape=True, recenter=True, **kwargs):
    """Rotate and offset an array.

    Same as `rotate` in `scipy.ndimage.interpolation` except that it
    rotates around a center point given by `cen` keyword.
    The array is rotated in the plane defined by the two axes given by the
    `axes` parameter using spline interpolation of the requested order.
    
    Parameters
    ----------
    data : ndarray
        The input array.
    angle : float
        The rotation angle in degrees (rotates in CW direction).
    cen : tuple
        Center location around which to rotate image.
        Values are expected to be `(xcen, ycen)`.
    recenter : bool
        Do we want to reposition so that `cen` is the image center?
        
    Keyword Args
    ------------
    axes : tuple of 2 ints, optional
        The two axes that define the plane of rotation. Default is the first
        two axes.
    reshape : bool, optional
        If `reshape` is True, the output shape is adapted so that the input
        array is contained completely in the output. Default is True.
    order : int, optional
        The order of the spline interpolation, default is 1.
        The order has to be in the range 0-5.
    mode : str, optional
        Points outside the boundaries of the input are filled according
        to the given mode ('constant', 'nearest', 'reflect', 'mirror' or 'wrap').
        Default is 'constant'.
    cval : scalar, optional
        Value used for points outside the boundaries of the input if
        ``mode='constant'``. Default is 0.0
    prefilter : bool, optional
        The parameter prefilter determines if the input is pre-filtered with
        `spline_filter` before interpolation (necessary for spline
        interpolation of order > 1).  If False, it is assumed that the input is
        already filtered. Default is True.

    Returns
    -------
    rotate : ndarray or None
        The rotated data.

    """

    # Return input data if angle is set to None or 0
    # and if 
    if ((angle is None) or (angle==0)) and (cen is None):
        return data

    ndim = len(data.shape)
    if ndim==2:
        ny, nx = data.shape
        nz = 1
    elif ndim==3:
        nz, ny, nx = data.shape
    else:
        raise ValueError('Input image can only have 2 or 3 dimensions. \
                          Found {} dimensions.'.format(ndim))

    if 'axes' not in kwargs.keys():
        kwargs['axes'] = (2,1)
    kwargs['order'] = order
    kwargs['cval'] = cval

    xcen, ycen = (nx/2, ny/2)
    if cen is None:
        cen = (xcen, ycen)
    xcen_new, ycen_new = cen
    delx, dely = (xcen-xcen_new, ycen-ycen_new)

    # Reshape into a 3D array if nz=1
    data = data.reshape([nz,ny,nx])
    # Return rotate function if rotating about center
    if np.allclose((delx, dely), 0, atol=1e-5):
        return rotate(data, angle, reshape=reshape, **kwargs).squeeze()

    # Pad and then shift array
    new_shape = (int(ny+2*abs(dely)), int(nx+2*abs(delx)))
    images_shift = []
    for im in data:
        im_pad = pad_or_cut_to_size(im, new_shape, fill_val=cval)
        im_new = fshift(im_pad, delx, dely, cval=cval)
        images_shift.append(im_new)
    images_shift = np.array(images_shift)
    
    # Remove additional dimension in the case of single image
    #images_shift = images_shift.squeeze()
    
    # Rotate images
    # TODO: Should reshape=True or reshape=reshape?
    images_shrot = rotate(images_shift, angle, reshape=True, **kwargs)
    
    if reshape:
        return images_shrot.squeeze()
    else:
        # Shift back to it's location
        if recenter:
            images_rot = images_shrot
        else:
            images_rot = []
            for im in images_shrot:
                im_new = fshift(im, -delx, -dely, pad=True, cval=cval)
                images_rot.append(im_new)
            images_rot = np.array(images_rot)
    
        images_fin = []
        for im in images_rot:
            im_new = pad_or_cut_to_size(im, (ny,nx))
            images_fin.append(im_new)
        images_fin = np.array(images_fin)
    
        return images_fin.squeeze()

def frebin(image, dimensions=None, scale=None, total=True):
    """Fractional rebin
    
    Python port from the IDL frebin.pro
    Shrink or expand the size of a 1D or 2D array by an arbitary amount 
    using bilinear interpolation. Conserves flux by ensuring that each 
    input pixel is equally represented in the output array.

    Parameters
    ----------
    image : ndarray
        Input image, 1-d or 2-d ndarray.
    dimensions : tuple or None
        Desired size of output array (take priority over scale).
    scale : tuple or None
        Factor to scale output array size. A scale of 2 will increase
        the number of pixels by 2 (ie., finer pixel scale).
    total : bool
        Conserves the surface flux. If True, the output pixels 
        will be the sum of pixels within the appropriate box of 
        the input image. Otherwise, they will be the average.
    
    Returns
    -------
    ndarray
        The binned ndarray
    """

    if dimensions is not None:
        if isinstance(dimensions, float):
            dimensions = [int(dimensions)] * len(image.shape)
        elif isinstance(dimensions, int):
            dimensions = [dimensions] * len(image.shape)
        elif len(dimensions) != len(image.shape):
            raise RuntimeError("The number of input dimensions don't match the image shape.")
    elif scale is not None:
        if isinstance(scale, float) or isinstance(scale, int):
            dimensions = list(map(int, map(lambda x: x+0.5, map(lambda x: x*scale, image.shape))))
        elif len(scale) != len(image.shape):
            raise RuntimeError("The number of input dimensions don't match the image shape.")
        else:
            dimensions = [scale[i]*image.shape[i] for i in range(len(scale))]
    else:
        raise RuntimeError('Incorrect parameters to rebin.\n\frebin(image, dimensions=(x,y))\n\frebin(image, scale=a')
    #print(dimensions)


    shape = image.shape
    if len(shape)==1:
        nlout = 1
        nsout = dimensions[0]
        nsout = int(nsout+0.5)
        dimensions = [nsout]
    elif len(shape)==2:
        nlout, nsout = dimensions
        nlout = int(nlout+0.5)
        nsout = int(nsout+0.5)
        dimensions = [nlout, nsout]
    if len(shape) > 2:
        raise ValueError('Input image can only have 1 or 2 dimensions. Found {} dimensions.'.format(len(shape)))
    

    if nlout != 1:
        nl = shape[0]
        ns = shape[1]
    else:
        nl = nlout
        ns = shape[0]

    sbox = ns / float(nsout)
    lbox = nl / float(nlout)
    #print(sbox,lbox)

    # Contract by integer amount
    if (sbox.is_integer()) and (lbox.is_integer()):
        image = image.reshape((nl,ns))
        result = krebin(image, (nlout,nsout))
        if not total: result /= (sbox*lbox)
        if nl == 1:
            return result[0,:]
        else:
            return result

    ns1 = ns - 1
    nl1 = nl - 1

    if nl == 1:
        #1D case
        _log.debug("Rebinning to Dimension: %s" % nsout)
        result = np.zeros(nsout)
        for i in range(nsout):
            rstart = i * sbox
            istart = int(rstart)
            rstop = rstart + sbox

            if int(rstop) < ns1:
                istop = int(rstop)
            else:
                istop = ns1

            frac1 = float(rstart) - istart
            frac2 = 1.0 - (rstop - istop)

            #add pixel values from istart to istop and subtract fraction pixel
            #from istart to rstart and fraction pixel from rstop to istop
            result[i] = np.sum(image[istart:istop + 1]) - frac1 * image[istart] - frac2 * image[istop]

        if total:
            return result
        else:
            return result / (float(sbox) * lbox)
    else:
        _log.debug("Rebinning to Dimensions: %s, %s" % tuple(dimensions))
        #2D case, first bin in second dimension
        temp = np.zeros((nlout, ns))
        result = np.zeros((nsout, nlout))

        #first lines
        for i in range(nlout):
            rstart = i * lbox
            istart = int(rstart)
            rstop = rstart + lbox

            if int(rstop) < nl1:
                istop = int(rstop)
            else:
                istop = nl1

            frac1 = float(rstart) - istart
            frac2 = 1.0 - (rstop - istop)

            if istart == istop:
                temp[i, :] = (1.0 - frac1 - frac2) * image[istart, :]
            else:
                temp[i, :] = np.sum(image[istart:istop + 1, :], axis=0) -\
                             frac1 * image[istart, :] - frac2 * image[istop, :]

        temp = np.transpose(temp)

        #then samples
        for i in range(nsout):
            rstart = i * sbox
            istart = int(rstart)
            rstop = rstart + sbox

            if int(rstop) < ns1:
                istop = int(rstop)
            else:
                istop = ns1

            frac1 = float(rstart) - istart
            frac2 = 1.0 - (rstop - istop)

            if istart == istop:
                result[i, :] = (1. - frac1 - frac2) * temp[istart, :]
            else:
                result[i, :] = np.sum(temp[istart:istop + 1, :], axis=0) -\
                               frac1 * temp[istart, :] - frac2 * temp[istop, :]

        if total:
            return np.transpose(result)
        else:
            return np.transpose(result) / (sbox * lbox)


def image_rescale(HDUlist_or_filename, pixscale_out, pixscale_in=None, 
                  dist_in=None, dist_out=None, cen_star=True, shape_out=None):
    """ Rescale image flux

    Scale the flux and rebin an image to some new pixel scale and distance. 
    The object's physical units (AU) are assumed to be constant, so the 
    total angular size changes if the distance to the object changes.

    IT IS RECOMMENDED THAT UNITS BE IN PHOTONS/SEC/PIXEL (not mJy/arcsec)

    Parameters
    ==========
    HDUlist_or_filename : HDUList or str
        Input either an HDUList or file name.
    pixscale_out : float
        Desired pixel scale (asec/pix) of returned image. Will be saved in header info.
    dist_out : float
        Output distance (parsec) of object in image. Will be saved in header info.
    
    Keyword Args
    ============
    pixscale_in : float or None
        Input image pixel scale. If None, then tries to grab info from the header.
    args_in : tuple
        Two parameters consisting of the input image pixel scale and distance
        assumed to be in units of arcsec/pixel and parsecs, respectively
    args_out : tuple
        Same as args_in, but the new desired outputs.
    cen_star : bool
        Is the star placed in the central pixel? If so, then the stellar flux is 
        assumed to be a single pixel that is equal to the maximum flux in the
        image. Rather than rebinning that pixel, the total flux is pulled out
        and readded to the central pixel of the final image.
    shape_out : tuple, int, or None
        Desired size for the output array (ny,nx). If a single value, then will 
        create a 2-element tuple of the same value.

    Returns
    =======
        HDUlist of the new image.
    """

    if isinstance(HDUlist_or_filename, six.string_types):
        hdulist = fits.open(HDUlist_or_filename)
    elif isinstance(HDUlist_or_filename, fits.HDUList):
        hdulist = HDUlist_or_filename
    else:
        raise ValueError("Input must be a filename or HDUlist")
    
    header = hdulist[0].header
    # Try to update input pixel scale if it exists in header
    if pixscale_in is None:
        key_test = ['PIXELSCL','PIXSCALE']
        for k in key_test:
            if k in header:
                pixscale_in = header[k]
        if pixscale_in is None:
            raise KeyError("Cannot determine input image pixel scale.")

    # Try to update input distance if it exists in header
    if dist_in is None:
        key_test = ['DISTANCE','DIST']
        for k in key_test:
            if k in header:
                dist_in = header[k]

    # If output distance is not set, set to input distance
    if dist_out is None:
        dist_out = 'None' if dist_in is None else dist_in
        fratio = 1
    elif dist_in is None:
        raise ValueError('Input distance should not be None if output distance is specified.')
    else:
        fratio = dist_in / dist_out

    # Scale the input flux by inverse square law
    image = (hdulist[0].data) * fratio**2

    # If we move the image closer while assuming same number of pixels with
    # the same AU/pixel, then this implies we've increased the angle that 
    # the image subtends. So, each pixel would have a larger angular size.
    # New image scale in arcsec/pixel
    imscale_new = pixscale_in * fratio

    # Before rebinning, we want the flux in the central pixel to
    # always be in the central pixel (the star). So, let's save
    # and remove that flux then add back after the rebinning.
    if cen_star:
        mask_max = image==image.max()
        star_flux = image[mask_max][0]
        image[mask_max] = 0

    # Rebin the image to get a pixel scale that oversamples the detector pixels
    fact = imscale_new / pixscale_out
    image_new = frebin(image, scale=fact)

    # Restore stellar flux to the central pixel.
    ny, nx = image_new.shape
    if cen_star:
        image_new[ny//2, nx//2] += star_flux

    if shape_out is not None:
        image_new = pad_or_cut_to_size(image_new, shape_out)

    hdu_new = fits.PrimaryHDU(image_new)
    hdu_new.header = hdulist[0].header.copy()
    hdulist_new = fits.HDUList([hdu_new])
    hdulist_new[0].header['PIXELSCL'] = (pixscale_out, 'arcsec/pixel')
    hdulist_new[0].header['DISTANCE'] = (dist_out, 'parsecs')

    return hdulist_new


def model_to_hdulist(args_model, sp_star, bandpass):

    """HDUList from model FITS file.

    Convert disk model to an HDUList with units of photons/sec/pixel.
    If observed filter is different than input filter, we assume that
    the disk has a flat scattering, meaning it scales with stellar
    continuum. Pixel sizes and distances are left unchanged, and
    stored in header.

    Parameters
    ----------
    args_model - tuple
        Arguments describing the necessary model information:
            - fname   : Name of model file or an HDUList
            - scale0  : Pixel scale (in arcsec/pixel)
            - dist0   : Assumed model distance
            - wave_um : Wavelength of observation
            - units0  : Assumed flux units (ie., MJy/arcsec^2 or muJy/pixel)
    sp_star : :mod:`pysynphot.spectrum`
        A pysynphot spectrum of central star. Used to adjust observed
        photon flux if filter differs from model input
    bandpass : :mod:`pysynphot.obsbandpass`
        Output `Pysynphot` bandpass from instrument class. This corresponds 
        to the flux at the entrance pupil for the particular filter.
    """

    #filt, mask, pupil = args_inst
    fname, scale0, dist0, wave_um, units0 = args_model
    wave0 = wave_um * 1e4


    #### Read in the image, then convert from mJy/arcsec^2 to photons/sec/pixel

    if isinstance(fname, fits.HDUList):
        hdulist = fname
    else:
        # Open file
        hdulist = fits.open(fname)

    # Get rid of any non-standard header keywords
    hdu = fits.PrimaryHDU(hdulist[0].data)
    for k in hdulist[0].header.keys():
        try:
            hdu.header[k] = hdulist[0].header[k]
        except ValueError:
            pass
    hdulist = fits.HDUList(hdu)

    # Break apart units0
    units_list = units0.split('/')
    if 'mJy' in units_list[0]:
        units_pysyn = S.units.mJy()
    elif 'uJy' in units_list[0]:
        units_pysyn = S.units.muJy()
    elif 'nJy' in units_list[0]:
        units_pysyn = S.units.nJy()
    elif 'MJy' in units_list[0]:
        hdulist[0].data *= 1000 # Convert to Jy
        units_pysyn = S.units.Jy()
    elif 'Jy' in units_list[0]: # Jy should be last
        units_pysyn = S.units.Jy()
    else:
        errstr = "Do not recognize units0='{}'".format(units0)
        raise ValueError(errstr)

    # Convert from input units to photlam (photons/sec/cm^2/A/angular size)
    im = units_pysyn.ToPhotlam(wave0, hdulist[0].data)

    # We assume scattering is flat in photons/sec/A
    # This means everything scales with stellar continuum
    sp_star.convert('photlam')
    wstar, fstar = (sp_star.wave/1e4, sp_star.flux)

    # Compare observed wavelength to image wavelength
    wobs_um = bandpass.avgwave() / 1e4 # Current bandpass wavelength

    wdel = np.linspace(-0.1,0.1)
    f_obs = np.interp(wobs_um+wdel, wstar, fstar)
    f0    = np.interp(wave_um+wdel, wstar, fstar)
    im *= np.mean(f_obs / f0)

    # Convert to photons/sec/pixel
    im *= bandpass.equivwidth() * S.refs.PRIMARY_AREA
    # If input units are per arcsec^2 then scale by pixel scale
    # This will be ph/sec for each oversampled pixel
    if ('arcsec' in units_list[1]) or ('asec' in units_list[1]):
        im *= scale0**2
    elif 'mas' in units_list[1]:
        im *= (scale0*1000)**2

    # Save into HDUList
    hdulist[0].data = im

    hdulist[0].header['UNITS']    = 'photons/sec'
    hdulist[0].header['PIXELSCL'] = (scale0, 'arcsec/pixel')
    hdulist[0].header['DISTANCE'] = (dist0, 'parsecs')

    return hdulist


def distort_image(hdulist_or_filename, ext=0, to_frame='sci', fill_value=0, 
                  xnew_coords=None, ynew_coords=None, return_coords=False,
                  aper=None, sci_cen=None):
    """ Distort an image

    Apply SIAF instrument distortion to an image that is assumed to be in 
    its ideal coordinates. The header information should contain the relevant
    SIAF point information, such as SI instrument, aperture name, pixel scale,
    detector oversampling, and detector position ('sci' coords).

    This function then transforms the image to the new coordinate system using
    scipy's RegularGridInterpolator (linear interpolation).

    Parameters
    ----------
    hdulist_or_filename : str or HDUList
        A PSF from WebbPSF, either as an HDUlist object or as a filename
    ext : int
        Extension of HDUList to perform distortion on.
    fill_value : float or None
        Value used to fill in any blank space by the skewed PSF. Default = 0.
        If set to None, values outside the domain are extrapolated.
    to_frame : str
        Type of input coordinates. 

            * 'tel': arcsecs V2,V3
            * 'sci': pixels, in conventional DMS axes orientation
            * 'det': pixels, in raw detector read out axes orientation
            * 'idl': arcsecs relative to aperture reference location.

    xnew_coords : None or ndarray
        Array of x-values in new coordinate frame to interpolate onto.
        Can be a 1-dimensional array of unique values, in which case 
        the final image will be of size (ny_new, nx_new). Or a 2d array 
        that corresponds to full regular grid and has same shape as 
        `ynew_coords` (ny_new, nx_new). If set to None, then final image
        is same size as input image, and coordinate grid spans the min
        and max values of siaf_ap.convert(xidl,yidl,'idl',to_frame). 
    ynew_coords : None or ndarray
        Array of y-values in new coordinate frame to interpolate onto.
        Can be a 1-dimensional array of unique values, in which case 
        the final image will be of size (ny_new, nx_new). Or a 2d array 
        that corresponds to full regular grid and has same shape as 
        `xnew_coords` (ny_new, nx_new). If set to None, then final image
        is same size as input image, and coordinate grid spans the min
        and max values of siaf_ap.convert(xidl,yidl,'idl',to_frame). 
    return_coords : bool
        In addition to returning the final image, setting this to True
        will return the full set of new coordinates. Output will then
        be (psf_new, xnew, ynew), where all three array have the same
        shape.
    aper : None or :mod:`pysiaf.Aperture`
        Option to pass the SIAF aperture if it is already known or
        specified to save time on generating a new one. If set to None,
        then automatically determines a new `pysiaf` aperture based on
        information stored in the header.
    """

    import pysiaf
    from scipy.interpolate import RegularGridInterpolator

    def _get_default_siaf(instrument, aper_name):

        # Create new naming because SIAF requires special capitalization
        if instrument == "NIRCAM":
            siaf_name = "NIRCam"
        elif instrument == "NIRSPEC":
            siaf_name = "NIRSpec"
        else:
            siaf_name = instrument

        # Select a single SIAF aperture
        siaf = pysiaf.Siaf(siaf_name)
        aper = siaf.apertures[aper_name]

        return aper

    # Read in input PSF
    if isinstance(hdulist_or_filename, str):
        hdu_list = fits.open(hdulist_or_filename)
    elif isinstance(hdulist_or_filename, fits.HDUList):
        hdu_list = hdulist_or_filename
    else:
        raise ValueError("input must be a filename or HDUlist")

    if aper is None:
        # Log instrument and detector names
        instrument = hdu_list[0].header["INSTRUME"].upper()
        aper_name = hdu_list[0].header["APERNAME"].upper()
        # Pull default values
        aper = _get_default_siaf(instrument, aper_name)
    
    # Pixel scale information
    ny, nx = hdu_list[ext].shape
    pixelscale = hdu_list[ext].header["PIXELSCL"]  # the pixel scale carries the over-sample value
    oversamp   = hdu_list[ext].header["DET_SAMP"]  # PSF oversampling relative to detector 

    # Get 'sci' reference location where PSF is observed
    if sci_cen is None:
        xsci_cen = hdu_list[ext].header["DET_X"]  # center x location in pixels ('sci')
        ysci_cen = hdu_list[ext].header["DET_Y"]  # center y location in pixels ('sci')
    else:
        xsci_cen, ysci_cen = sci_cen

    # ###############################################
    # Create an array of indices (in pixels) for where the PSF is located on the detector
    nx_half, ny_half = ( (nx-1)/2., (ny-1)/2. )
    xlin = np.linspace(-1*nx_half, nx_half, nx)
    ylin = np.linspace(-1*ny_half, ny_half, ny)
    xarr, yarr = np.meshgrid(xlin, ylin) 

    # Convert the PSF center point from pixels to arcseconds using pysiaf
    xidl_cen, yidl_cen = aper.sci_to_idl(xsci_cen, ysci_cen)

    # Get 'idl' coords
    xidl = xarr * pixelscale + xidl_cen
    yidl = yarr * pixelscale + yidl_cen

    # ###############################################
    # Create an array of indices (in pixels) that the final data will be interpolated onto
    xnew_cen, ynew_cen = aper.convert(xsci_cen, ysci_cen, 'sci', to_frame)
    # If new x and y values are specified, create a meshgrid
    if (xnew_coords is not None) and (ynew_coords is not None):
        if len(xnew_coords.shape)==1 and len(ynew_coords.shape)==1:
            xnew, ynew = np.meshgrid(xnew_coords, ynew_coords)
        elif len(xnew_coords.shape)==2 and len(ynew_coords.shape)==2:
            assert xnew_coords.shape==ynew_coords.shape, "If new x and y inputs are a grid, must be same shapes"
            xnew, ynew = xnew_coords, ynew_coords
    elif to_frame=='sci':
        xnew = xarr / oversamp + xnew_cen
        ynew = yarr / oversamp + ynew_cen
    else:
        xv, yv = aper.convert(xidl, yidl, 'idl', to_frame)
        xmin, xmax = (xv.min(), xv.max())
        ymin, ymax = (yv.min(), yv.max())
        
        # Range xnew from 0 to 1
        xnew = xarr - xarr.min()
        xnew /= xnew.max()
        # Set to xmin to xmax
        xnew = xnew * (xmax - xmin) + xmin
        # Make sure center value is xnew_cen
        xnew += xnew_cen - np.median(xnew)

        # Range ynew from 0 to 1
        ynew = yarr - yarr.min()
        ynew /= ynew.max()
        # Set to ymin to ymax
        ynew = ynew * (ymax - ymin) + ymin
        # Make sure center value is xnew_cen
        ynew += ynew_cen - np.median(ynew)
    
    # Convert requested coordinates to 'idl' coordinates
    xnew_idl, ynew_idl = aper.convert(xnew, ynew, to_frame, 'idl')

    # ###############################################
    # Interpolate using Regular Grid Interpolator
    xvals = xlin * pixelscale + xidl_cen
    yvals = ylin * pixelscale + yidl_cen
    func = RegularGridInterpolator((yvals,xvals), hdu_list[ext].data, method='linear', 
                                   bounds_error=False, fill_value=fill_value)

    # Create an array of (yidl, xidl) values to interpolate onto
    pts = np.array([ynew_idl.flatten(),xnew_idl.flatten()]).transpose()
    psf_new = func(pts).reshape(xnew.shape)
    
    if return_coords:
        return (psf_new, xnew, ynew)
    else:
        return psf_new

def _convolve_psfs_for_mp(arg_vals):
    """
    Internal helper routine for parallelizing computations across multiple processors,
    specifically for convolving position-dependent PSFs with an extended image or
    field of PSFs.

    """
    
    im, psf, ind_mask = arg_vals
    im_temp = im.copy()
    im_temp[~ind_mask] = 0
    
    if np.allclose(im_temp,0):
        # No need to convolve anything if no flux!
        res = im_temp
    else:
        # Normalize PSF sum to 1.0
        # Otherwise convolve_fft may throw an error if psf.sum() is too small
        norm = psf.sum()
        psf = psf / norm
        res = convolve_fft(im_temp, psf, fftn=fftpack.fftn, ifftn=fftpack.ifftn, allow_huge=True)
        res *= norm

    return res

def convolve_image(hdul_sci_image, hdul_psfs, aper=None):
    """ Convolve image with various PSFs

    """
    
    import pysiaf
    
    # Get SIAF aperture info
    hdr = hdul_psfs[0].header
    if aper is None:
        siaf = pysiaf.siaf.Siaf(hdr['INSTRUME'])
        siaf_ap = siaf[hdr['APERNAME']]
    else:
        siaf_ap = aper
    
    # Get xsci and ysci coordinates
    xvals = np.array([hdu.header['XVAL'] for hdu in hdul_psfs])
    yvals = np.array([hdu.header['YVAL'] for hdu in hdul_psfs])
    if 'sci' in hdr['CFRAME']:
        xsci, ysci = (xvals, yvals)
    else:
        xsci, ysci = siaf_ap.convert(xvals, yvals, hdr['CFRAME'], 'sci')
    
    xoff_sci_asec_psfs = (xsci - siaf_ap.XSciRef) * siaf_ap.XSciScale
    yoff_sci_asec_psfs = (ysci - siaf_ap.YSciRef) * siaf_ap.YSciScale
    
    # Size of input image in arcsec
    im_input = hdul_sci_image[0].data
    pixscale = hdul_sci_image[0].header['PIXELSCL']
    ysize, xsize = im_input.shape
    ysize_asec = ysize * pixscale
    xsize_asec = xsize * pixscale
    
    # Create mask for input image for each PSF to convolve
    rho_arr = []
    coords_asec = (xoff_sci_asec_psfs, yoff_sci_asec_psfs)
    for xv, yv in np.transpose(coords_asec):
        cen = (xsize_asec/2 + xv, ysize_asec/2 + yv)
        yarr, xarr = np.indices((ysize,xsize))
        xarr = xarr*pixscale - cen[0]
        yarr = yarr*pixscale - cen[1]
        rho = np.sqrt(xarr**2 + yarr**2)
        rho_arr.append(rho)
    rho_arr = np.array(rho_arr)

    # Calculate indices corresponding to closest PSF
    im_indices = np.argmin(rho_arr, axis=0)
    del rho_arr

    # Create an image mask for each PSF
    npsf = len(hdul_psfs)
    mask_arr = np.array([im_indices==i for i in range(npsf)])
    
    # Split into workers
    worker_args = [(im_input, hdul_psfs[i].data, mask_arr[i]) for i in range(npsf)]

    nsplit = 4
    if nsplit>1:
        im_conv = []
        try:
            with mp.Pool(nsplit) as pool:
                for res in tqdm(pool.imap_unordered(_convolve_psfs_for_mp, worker_args), total=npsf):
                    im_conv.append(res)
                pool.close()
            if im_conv[0] is None:
                raise RuntimeError('Returned None values. Issue with multiprocess??')
        except Exception as e:
            print('Caught an exception during multiprocess.')
            print('Closing multiprocess pool.')
            pool.terminate()
            pool.close()
            raise e
        else:
            print('Closing multiprocess pool.')

        im_conv = np.array(im_conv).sum(axis=0)
    else:
        im_conv = np.sum(np.array([_convolve_psfs_for_mp(wa) for wa in tqdm(worker_args)]), axis=0)

    return im_conv

def make_disk_image(inst, disk_params, sp_star=None, pixscale_out=None, dist_out=None,
                    shape_out=None):
    """
    Rescale disk model flux to desired pixel scale and distance.
    If instrument bandpass is different from disk model, scales 
    flux assuming a grey scattering model.

    Returns image flux values in photons/sec.
    
    Parameters
    ==========
    inst : mod::webbpsf_ext instrument class
        E.g. NIRCam_ext, MIRI_ext classes
    disk_params : dict
        Arguments describing the necessary model information:
            - 'file'       : Path to model file or an HDUList.
            - 'pixscale'   : Pixel scale (arcsec/pixel).
            - 'dist'       : Assumed model distance in parsecs.
            - 'wavelength' : Wavelength of observation in microns.
            - 'units'      : String of assumed flux units (ie., MJy/arcsec^2 or muJy/pixel)
            - 'cen_star'   : True/False. Is the star placed in the central pixel? 
        Will Convert from [M,m,u,n]Jy/[arcsec^2,pixel] to photons/sec/pixel

    Keyword Args
    ============
    sp_star : :mod:`pysynphot.spectrum`
        A pysynphot spectrum of central star. Used to adjust observed
        photon flux if filter differs from model input
    pixscale_out : float
        Desired pixelscale of returned image.
    dist_out : float
        Distance to place disk at. Flux is scaled appropriately relative to
        the input distance specified in `disk_params`.
    shape_out : tuple, int, or None
        Desired size for the output array (ny,nx). If a single value, then will 
        create a 2-element tuple of the same value.
    """

    from .spectra import stellar_spectrum
    
    # Get stellar spectrum
    if sp_star is None:
        sp_star = stellar_spectrum('flat')
        
    # Set desired distance to be the same as the stellar object
    if dist_out is None:
        dist_out = disk_params['dist']
    
    # Create disk image for input bandpass from model
    keys = ['file', 'pixscale', 'dist', 'wavelength', 'units']
    args_model = tuple(disk_params[k] for k in keys)

    # Open model file and scale disk emission to new bandpass, assuming grey scattering properties
    hdul_model = model_to_hdulist(args_model, sp_star, inst.bandpass)

    # Change pixel scale (default is same as inst pixel oversampling)
    # Provide option to move disk to a different distance
    # `dist_in` and `pixscale_in` will be pulled from HDUList header
    if pixscale_out is None:
        pixscale_out = inst.pixelscale / inst.oversample
    hdul_disk_image = image_rescale(hdul_model, pixscale_out, dist_out=dist_out, 
                                    cen_star=disk_params['cen_star'], shape_out=shape_out)

    copy_keys = [
        'INSTRUME', 'APERNAME', 'FILTER', 'DET_SAMP',
        'DET_NAME', 'DET_X', 'DET_Y', 'DET_V2', 'DET_V3',
    ]
    head_temp = inst.psf_coeff_header
    for key in copy_keys:
        try:
            hdul_disk_image[0].header[key] = (head_temp[key], head_temp.comments[key])
        except (AttributeError, KeyError):
            pass
        
    return hdul_disk_image

def rotate_shift_image(hdul, PA_offset=0, delx_asec=0, dely_asec=0):
    """ Rotate/Shift image
    
    Rotate 
    
    PA_offset : float
        Rotate entire scene by some position angle. 
        Positive values are counter-clockwise.
    """
        
    # Rotate
    if np.abs(PA_offset)!=0:
        im_rot = rotate(hdul[0].data, -1*PA_offset, reshape=False, order=1)
    else:
        im_rot = hdul[0].data
    delx, dely = np.array([delx_asec, dely_asec]) / hdul[0].header['PIXELSCL']
    
    # Get position offsets
    im_new = fshift(im_rot, delx, dely, pad=True)
    
    hdu_new = fits.PrimaryHDU(im_new)
    hdu_new.header = hdul[0].header
    
    return fits.HDUList(hdu_new)

