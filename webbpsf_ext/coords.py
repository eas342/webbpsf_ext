import numpy as np
import logging
_log = logging.getLogger('pynrc')

__epsilon = np.finfo(float).eps

from .utils import webbpsf, poppy, pysiaf

def dist_image(image, pixscale=None, center=None, return_theta=False):
    """Pixel distances
    
    Returns radial distance in units of pixels, unless pixscale is specified.
    Use the center keyword to specify the position (in pixels) to measure from.
    If not set, then the center of the image is used.

    return_theta will also return the angular position of each pixel relative 
    to the specified center
    
    Parameters
    ----------
    image : ndarray
        Input image to find pixel distances (and theta).
    pixscale : int, None
        Pixel scale (such as arcsec/pixel or AU/pixel) that
        dictates the units of the output distances. If None,
        then values are in units of pixels.
    center : tuple
        Pixel location (x,y) in the array calculate distance. If set 
        to None, then the default is the array center pixel.
    return_theta : bool
        Also return the angular positions as a 2nd element.
    """
    y, x = np.indices(image.shape)
    if center is None:
        center = tuple((a - 1) / 2.0 for a in image.shape[::-1])
    x = x - center[0]
    y = y - center[1]

    rho = np.sqrt(x**2 + y**2)
    if pixscale is not None: 
        rho *= pixscale

    if return_theta:
        return rho, np.arctan2(-x,y)*180/np.pi
    else:
        return rho

def xy_to_rtheta(x, y):
    """Convert (x,y) to (r,theta)
    
    Input (x,y) coordinates and return polar cooridnates that use
    the WebbPSF convention (theta is CCW of +Y)
    
    Input can either be a single value or numpy array.

    Parameters
    ----------
    x : float or array
        X location values
    y : float or array
        Y location values
    """
    r = np.sqrt(x**2 + y**2)
    theta = np.arctan2(-x,y)*180/np.pi

    if np.size(r)==1:
        if np.abs(x) < __epsilon: x = 0
        if np.abs(y) < __epsilon: y = 0
    else:
        r[np.abs(r) < __epsilon] = 0
        theta[np.abs(theta) < __epsilon] = 0

    return r, theta

def rtheta_to_xy(r, theta):
    """Convert (r,theta) to (x,y)
    
    Input polar cooridnates (WebbPSF convention) and return Carteesian coords
    in the imaging coordinate system (as opposed to RA/DEC)

    Input can either be a single value or numpy array.

    Parameters
    ----------
    r : float or array
        Radial offset from the center in pixels
    theta : float or array
        Position angle for offset in degrees CCW (+Y).
    """
    x = -r * np.sin(theta*np.pi/180.)
    y =  r * np.cos(theta*np.pi/180.)

    if np.size(x)==1:
        if np.abs(x) < __epsilon: x = 0
        if np.abs(y) < __epsilon: y = 0
    else:
        x[np.abs(x) < __epsilon] = 0
        y[np.abs(y) < __epsilon] = 0

    return x, y
    
def xy_rot(x, y, ang):

    """Rotate (x,y) positions to new coords
    
    Rotate (x,y) values by some angle. 
    Positive ang values rotate counter-clockwise.
    
    Parameters
    -----------
    x : float or array
        X location values
    y : float or array
        Y location values
    ang : float or array
        Rotation angle in degrees CCW
    """

    r, theta = xy_to_rtheta(x, y)    
    return rtheta_to_xy(r, theta+ang)


###########################################################################
#    NIRCam SIAF helper functions
###########################################################################


# NIRCam aperture limits 
def get_NRC_v2v3_limits(pupil=None, border=10, return_corners=False, **kwargs):
    """
    V2/V3 Limits for a given module stored within an dictionary

    border : float
        Extend a border by some number of arcsec.
    return_corners : bool
        Return the actual aperture corners.
        Otherwise, values are chosen to be a square in V2/V3.
    """
    
    import pysiaf
    siaf = pysiaf.Siaf('NIRCam')
    siaf.generate_toc()

    names_dict = {
        'SW' : 'NRCALL_FULL',
        'LW' : 'NRCALL_FULL',
        'SWA': 'NRCAS_FULL', 
        'SWB': 'NRCBS_FULL',
        'LWA': 'NRCA5_FULL',
        'LWB': 'NRCB5_FULL',
    }

    v2v3_limits = {}
    for name in names_dict.keys():
       
        apname = names_dict[name]

        # Do all four apertures for each SWA & SWB
        ap = siaf[apname]
        if ('S_' in apname) or ('ALL_' in apname):
            v2_ref, v3_ref = ap.corners('tel', False)
        else:
            xsci, ysci = ap.corners('sci', False)
            v2_ref, v3_ref = ap.sci_to_tel(xsci, ysci)

        # Offset by 50" if coronagraphy
        if (pupil is not None) and ('LYOT' in pupil):
            v2_ref -= 2.1
            v3_ref += 47.7

        # Add border margin
        v2_avg = np.mean(v2_ref)
        v2_ref[v2_ref<v2_avg] -= border
        v2_ref[v2_ref>v2_avg] += border
        v3_avg = np.mean(v3_ref)
        v3_ref[v3_ref<v3_avg] -= border
        v3_ref[v3_ref>v3_avg] += border

        if return_corners:

            v2v3_limits[name] = {'V2': v2_ref / 60.,
                                 'V3': v3_ref / 60.}
        else:
            v2_minmax = np.array([v2_ref.min(), v2_ref.max()])
            v3_minmax = np.array([v3_ref.min(), v3_ref.max()])
            v2v3_limits[name] = {'V2': v2_minmax / 60.,
                                 'V3': v3_minmax / 60.}
        
    return v2v3_limits

def NIRCam_V2V3_limits(module, channel='LW', pupil=None, rederive=False, return_corners=False, **kwargs):
    """
    NIRCam V2/V3 bounds +10" border encompassing detector.
    """

    # Grab coordinate from pySIAF
    if rederive:
        v2v3_limits = get_NRC_v2v3_limits(pupil=pupil, return_corners=return_corners, **kwargs)

        name = channel + module
        if return_corners:
            return v2v3_limits[name]['V2'], v2v3_limits[name]['V3']
        else:
            v2_min, v2_max = v2v3_limits[name]['V2']
            v3_min, v3_max = v2v3_limits[name]['V3']
    else: # Or use preset coordinates
        if module=='A':
            v2_min, v2_max, v3_min, v3_max = (0.2, 2.7, -9.5, -7.0)
        else:
            v2_min, v2_max, v3_min, v3_max = (-2.7, -0.2, -9.5, -7.0)

        if return_corners:
            return np.array([v2_min, v2_min, v2_max, v2_max]), np.array([v3_min, v3_max, v3_min, v3_max])

    return v2_min, v2_max, v3_min, v3_max 




def ap_radec(ap_obs, ap_ref, coord_ref, pa, base_off=(0,0), dith_off=(0,0),
             get_cenpos=True, get_vert=False):
    """Aperture reference point(s) RA/Dec
    
    Given the (RA, Dec) and position angle of a given reference aperture,
    return the (RA, Dec) associated with the reference point (usually center) 
    of a different aperture. Can also return the corner vertices of the
    aperture. 
    
    Typically, the reference aperture (ap_ref) is used for the telescope 
    pointing information (e.g., NRCALL), but you may want to determine
    locations of the individual detector apertures (NRCA1_FULL, NRCB3_FULL, etc).
    
    Parameters
    ----------
    ap_obs : str
        Name of observed aperture (e.g., NRCA5_FULL)
    ap_ref : str
        Name of reference aperture (e.g., NRCALL_FULL)
    coord_ref : tuple or list
        Center position of reference aperture (RA/Dec deg)
    pa : float
        Position angle in degrees measured from North to V3 axis in North to East direction.
        
    Keywords
    --------
    base_off : list or tuple
        X/Y offset of overall aperture offset (see APT pointing file)
    dither_off : list or tuple
        Additional offset from dithering (see APT pointing file)
    get_cenpos : bool
        Return aperture reference location coordinates?
    get_vert: bool
        Return closed polygon vertices (useful for plotting)?
    """

    if (get_cenpos==False) and (get_vert==False):
        _log.warning("Neither get_cenpos nor get_vert were set to True. Nothing to return.")
        return

    si_match = {'NRC': 'nircam', 'NIS': 'niriss', 'MIR': 'miri', 'NRS': 'nirspec', 'FGS': 'fgs'}
    siaf_obs = pysiaf.Siaf(si_match.get(ap_obs[0:3]))
    siaf_ref = pysiaf.Siaf(si_match.get(ap_ref[0:3]))
    ap_siaf_ref = siaf_ref[ap_ref]
    ap_siaf_obs = siaf_obs[ap_obs]

    # RA and Dec of ap ref location and the objects in the field
    ra_ref, dec_ref = coord_ref

    # Field offset as specified in APT Special Requirements
    # These appear to be defined in 'idl' coords
    x_off, y_off  = (base_off[0] + dith_off[0], base_off[1] + dith_off[1])

    # V2/V3 reference location aligned with RA/Dec reference
    # and offset by (x_off, y_off) in 'idl' coords
    v2_ref, v3_ref = np.array(ap_siaf_ref.convert(x_off, y_off, 'idl', 'tel'))

    # Attitude correction matrix relative to reference aperture
    att = pysiaf.utils.rotations.attitude(v2_ref, v3_ref, ra_ref, dec_ref, pa)

    # Get V2/V3 position of observed SIAF aperture and convert to RA/Dec
    if get_cenpos==True:
        v2_obs, v3_obs  = ap_siaf_obs.reference_point('tel')
        ra_obs, dec_obs = pysiaf.utils.rotations.pointing(att, v2_obs, v3_obs)
        cen_obs = (ra_obs, dec_obs)
    
    # Get V2/V3 vertices of observed SIAF aperture and convert to RA/Dec
    if get_vert==True:
        v2_vert, v3_vert  = ap_siaf_obs.closed_polygon_points('tel', rederive=False)
        ra_vert, dec_vert = pysiaf.utils.rotations.pointing(att, v2_vert, v3_vert)
        vert_obs = (ra_vert, dec_vert)

    if (get_cenpos==True) and (get_vert==True):
        return cen_obs, vert_obs
    elif get_cenpos==True:
        return cen_obs
    elif get_vert==True:
        return vert_obs
    else:
        _log.warning("Neither get_cenpos nor get_vert were set to True. Nothing to return.")
        return


def radec_to_v2v3(coord_objs, siaf_ref_name, coord_ref, pa_ref, base_off=(0,0), dith_off=(0,0)):
    """RA/Dec to V2/V3
    
    Convert a series of RA/Dec positions to telescope V2/V3 coordinates (in arcsec).
    
    Parameters
    ----------
    coord_objs : tuple 
        (RA, Dec) positions (deg), where RA and Dec are numpy arrays.    
    siaf_ref_name : str
        Reference SIAF aperture name (e.g., 'NRCALL_FULL') 
    coord_ref : list or tuple
        RA and Dec towards which reference SIAF points
    pa : float
        Position angle in degrees measured from North to V3 axis in North to East direction.
        
    Keywords
    --------
    base_off : list or tuple
        X/Y offset of overall aperture offset (see APT pointing file)
    dither_off : list or tuple
        Additional offset from dithering (see APT pointing file)
    """
    
    # SIAF object setup
    si_match = {'NRC': 'nircam', 'NIS': 'niriss', 'MIR': 'miri', 'NRS': 'nirspec', 'FGS': 'fgs'}
    siaf_ref = pysiaf.Siaf(si_match.get(siaf_ref_name[0:3]))
    siaf_ap = siaf_ref[siaf_ref_name]
    
    # RA and Dec of ap ref location and the objects in the field
    ra_ref, dec_ref = coord_ref
    ra_obj, dec_obj = coord_objs

    # Field offset as specified in APT Special Requirements
    # These appear to be defined in 'idl' coords
    x_off, y_off  = (base_off[0] + dith_off[0], base_off[1] + dith_off[1])

    # V2/V3 reference location aligned with RA/Dec reference
    # and offset by (x_off, y_off) in 'idl' coords
    v2_ref, v3_ref = np.array(siaf_ap.convert(x_off, y_off, 'idl', 'tel'))

    # Attitude correction matrix relative to NRCALL_FULL aperture
    att = pysiaf.utils.rotations.attitude(v2_ref-x_off, v3_ref+y_off, ra_ref, dec_ref, pa_ref)

    # Convert all RA/Dec coordinates into V2/V3 positions for objects
    v2_obj, v3_obj = pysiaf.utils.rotations.getv2v3(att, ra_obj, dec_obj)

    return (v2_obj, v3_obj)


def v2v3_to_pixel(ap_obs, v2_obj, v3_obj, frame='sci'):
    """V2/V3 to pixel coordinates
    
    Convert object V2/V3 coordinates into pixel positions.

    Parameters
    ==========
    ap_obs : str
        Name of observed aperture (e.g., NRCA5_FULL)
    v2_obj : ndarray
        V2 locations of stellar sources.
    v3_obj : ndarray
        V3 locations of stellar sources.

    Keywords
    ========
    frame : str
        'det' or 'sci' coordinate frame. 'det' is always full frame reference.
        'sci' is relative to subarray size if not a full frame aperture.
    """
    
    # SIAF object setup
    si_match = {'NRC': 'nircam', 'NIS': 'niriss', 'MIR': 'miri', 'NRS': 'nirspec', 'FGS': 'fgs'}
    siaf = pysiaf.Siaf(si_match.get(ap_obs[0:3]))
    ap_siaf = siaf[ap_obs]

    if frame=='det':
        xpix, ypix = ap_siaf.tel_to_det(v2_obj, v3_obj)
    elif frame=='sci':
        xpix, ypix = ap_siaf.tel_to_sci(v2_obj, v3_obj)
    else:
        raise ValueError("Do not recognize frame keyword value: {}".format(frame))
        
    return (xpix, ypix)


def gen_sgd_offsets(sgd_type, slew_std=5, fsm_std=2.5):
    """
    Create a series of x and y position offsets for a SGD pattern.
    This includes the central position as the first in the series.
    By default, will also add random movement errors using the
    `slew_std` and `fsm_std` keywords.
    
    Parameters
    ==========
    sgd_type : str
        Small grid dither pattern. Valid types are
        '9circle', '5box', '5diamond', '3bar', '5miri', and '9miri'
        where the first four refer to NIRCam coronagraphyic dither
        positions and the last two are for MIRI coronagraphy.
    fsm_std : float
        One-sigma accuracy per axis of fine steering mirror positions.
        This provides randomness to each position relative to the nominal 
        central position. Ignored for central position. Values are in mas. 
    slew_std : float
        One-sigma accuracy per axis of the initial slew. This is applied
        to all positions and gives a baseline offset relative to the
        desired mask center. Values are in mas.
    """
    
    if sgd_type=='9circle':
        xoff_msec = np.array([0.0,  0,-15,-20,-15,  0,+15,+20,+15])
        yoff_msec = np.array([0.0,+20,+15,  0,-15,-20,-15,  0,+15])
    elif sgd_type=='5box':
        xoff_msec = np.array([0.0,+15,-15,-15,+15])
        yoff_msec = np.array([0.0,+15,+15,-15,-15])
    elif sgd_type=='5diamond':
        xoff_msec = np.array([0.0,  0,  0,  0,  0])
        yoff_msec = np.array([0.0,+20,-20,+20,-20])
    elif sgd_type=='5bar':
        xoff_msec = np.array([0.0,  0,  0,  0,  0])
        yoff_msec = np.array([0.0,+20,+10,-10,-20])
    elif sgd_type=='3bar':
        xoff_msec = np.array([0.0,  0,  0])
        yoff_msec = np.array([0.0,+15,-15])
    elif sgd_type=='5miri':
        xoff_msec = np.array([0.0,-10,+10,+10,-10])
        yoff_msec = np.array([0.0,+10,+10,-10,-10])
    elif sgd_type=='9miri':
        xoff_msec = np.array([0.0,-10,-10,  0,+10,+10,+10,  0,-10])
        yoff_msec = np.array([0.0,  0,+10,+10,+10,  0,-10,-10,-10])
    else:
        raise ValueError(f"{sgd_type} not a valid SGD type")
        
    # Add randomized telescope offsets
    if slew_std>0:
        x_point, y_point = np.random.normal(scale=slew_std, size=2)
        xoff_msec += x_point
        yoff_msec += y_point

    # Add randomized FSM offsets
    if fsm_std>0:
        x_fsm = np.random.normal(scale=fsm_std, size=xoff_msec.shape)
        y_fsm = np.random.normal(scale=fsm_std, size=yoff_msec.shape)
        xoff_msec[1:] += x_fsm[1:]
        yoff_msec[1:] += y_fsm[1:]
    
    return xoff_msec / 1000, yoff_msec / 1000


def get_idl_offset(base_offset=(0,0), dith_offset=(0,0), base_std=0, use_ta=True, 
                   dith_std=0, use_sgd=True, **kwargs):
    """
    Calculate pointing offsets in 'idl' coordinates. Inputs come from the
    APT's .pointing file. For a sequence of dithers, make sure to only
    calculate the base offset once, and all dithers independently. For
    instance:
    
        >>> base_offset = get_idl_offset(base_std=None)
        >>> dith1 = get_idl_offset(base_offset, dith_offset=(-0.01,+0.01), dith_std=None)
        >>> dith2 = get_idl_offset(base_offset, dith_offset=(+0.01,+0.01), dith_std=None)
        >>> dith3 = get_idl_offset(base_offset, dith_offset=(+0.01,-0.01), dith_std=None)
        >>> dith4 = get_idl_offset(base_offset, dith_offset=(-0.01,-0.01), dith_std=None)
    
    Parameters
    ==========
    base_offset : array-like
        Corresponds to (BaseX, BaseY) columns in .pointing file. 
    dith_offset : array-like
        Corresponds to (DithX, DithY ) columns in .pointing file. 
    base_std : float or array-like or None
        The 1-sigma pointing uncertainty per axis for telescope slew. 
        If None, then standard deviation is chosen to be either 5 mas 
        or 100 mas, depending on `use_ta` setting.
    use_ta : bool
        If observation uses a target acquisition, then assume only 5 mas
        of pointing uncertainty, other 100 mas for "blind" pointing.
    base_std : float or array-like or None
        The 1-sigma pointing uncertainty per axis for dithers. If None,
        then standard deviation is chosen to be either 2.5 or 5 mas, 
        depending on `use_sgd` setting.
    use_sgd : bool
        If True, then we're employing small-grid dithers with the fine
        steering mirror, which has a ~2.5 mas uncertainty. Otherwise,
        assume standard small angle maneuver, which has ~5 mas uncertainty.
    """
    

    # Convert to arrays (values of mas)
    base_xy = np.asarray(base_offset, dtype='float') * 1000
    dith_xy = np.asarray(dith_offset, dtype='float') * 1000

    # Set telescope slew uncertainty
    if base_std is None:
        base_std = 5.0 if use_ta else 100
    
    # No dither offset 
    if dith_xy[0]==dith_xy[1]==0:
        dith_std = 0
    elif (dith_std is None):
        dith_std = 2.5 if use_sgd else 5.0
    
    offset = np.random.normal(loc=base_xy, scale=base_std) + np.random.normal(loc=dith_xy, scale=dith_std)
    return offset / 1000