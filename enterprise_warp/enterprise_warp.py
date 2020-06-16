#!/bin/python

import numpy as np
import json
import glob
import os
import optparse

import enterprise.signals.parameter as parameter
from enterprise.signals import utils
from enterprise.signals import selections
from enterprise.signals import signal_base
from enterprise.signals import deterministic_signals
import enterprise.signals.white_signals as white_signals
import enterprise.signals.gp_signals as gp_signals
from enterprise.pulsar import Pulsar
import enterprise.constants as const
import libstempo as T2
from enterprise_extensions import models

from enterprise_models import StandardModels

#import LT_custom
#import model_constants as mc

from scipy.special import ndtri

from matplotlib import pyplot as plt

def parse_commandline():
  """@parse the options given on the command-line.
  """
  parser = optparse.OptionParser()

  parser.add_option("-n", "--num", help="Pulsar number",  default=0, type=int)
  parser.add_option("-p", "--prfile", help="Parameter file", type=str)
  parser.add_option("-q", "--prfile2", help="Parameter file 2 for Bayes factor", default=None, type=str)
  parser.add_option("-i", "--images", help="Plots", default=1, type=int)

  parser.add_option("-o", "--onum", help="Other number",  default=0, type=int)
  parser.add_option("-c", "--sn_fcpr", help="Option to fix corner frequency parameter", default=None, type=float)

  opts, args = parser.parse_args()

  return opts

class ModelParams(object):
  def __init__(self,model_id):
    self.model_id = model_id

class Params(object):
  # Load parameters for how to run Enterprise
  def __init__(self, input_file_name, opts=None, custom_models_obj=None):
    self.input_file_name = input_file_name
    self.opts = opts
    self.psrs = list()
    self.Tspan = None
    self.custom_models_obj = custom_models_obj
    self.label_attr_map = {
      "datadir:": ["datadir", str],
      "out:": ["out", str],
      "overwrite:": ["overwrite", str],
      "allpulsars:": ["allpulsars", str],
      "noisefiles:": ["noisefiles", str],
      "noise_model_file:": ["noise_model_file", str],
      "sampler:": ["sampler", str],
      "dlogz:": ["dlogz", float],
      "nsamp:": ["nsamp", int],
      "nwalk:": ["nwalk", int],
      "ntemp:": ["ntemp", int],
      "setupsamp:": ["setupsamp", bool],
      "psrlist:": ["psrlist", str],
      "psrcachedir:": ["psrcachedir", str],
      "ssephem:": ["ssephem", str],
      "clock:": ["clock", str],
      "AMweight:": ["AMweight", int],
      "DMweight:": ["DMweight", int],
      "SCAMweight:": ["SCAMweight", int],
      "custom_commonpsr:": ["custom_commonpsr", str],
      "custom_singlepsr:": ["custom_singlepsr", str],
      "tm:": ["tm", str],
      "fref:": ["fref", str]
}
    if self.custom_models_obj is not None:
      self.noise_model_obj = self.custom_models_obj
    else:
      self.noise_model_obj = StandardModels
    self.label_attr_map.update( self.noise_model_obj().get_label_attr_map() )
    model_id = None
    self.model_ids = list()
    self.__dict__['models'] = dict()

    with open(input_file_name, 'r') as input_file:
      for line in input_file:
        between_curly_brackets = line[line.find('{')+1 : line.find('}')]
        if between_curly_brackets.isdigit():
          model_id = int(between_curly_brackets)
          self.create_model(model_id)
          continue

        row = line.split()
        label = row[0]
        data = row[1:]  # rest of row is data list
        attr = self.label_attr_map[label][0]
        datatypes = self.label_attr_map[label][1:]
        if len(datatypes)==1 and len(data)>1:
          datatypes = [datatypes[0] for dd in data]

        values = [(datatypes[i](data[i])) for i in range(len(data))]

        if model_id == None:
          self.__dict__[attr] = values if len(values) > 1 else values[0]
        else:
          self.models[model_id].__dict__[attr] = \
                                values if len(values) > 1 else values[0]

    if not self.models:
      model_id = 0
      self.create_model(model_id)
    self.label = os.path.basename(os.path.normpath(self.out))
    self.override_params_using_opts()
    self.set_default_params()
    self.read_modeldicts()
    self.clone_all_params_to_models()

  def override_params_using_opts(self):
    ''' If opts from command line parser has a non-None parameter argument,
    override this parameter for all models'''
    if self.opts is not None:
      for key, val in self.models.items():
        for opt in self.opts.__dict__:
          if opt in self.models[key].__dict__ \
                  and self.opts.__dict__[opt] is not None:
            self.models[key].__dict__[opt] = self.opts.__dict__[opt]
            self.label+='_'+opt+'_'+str(self.opts.__dict__[opt])
            print('Model: ',key,'. Overriding parameter ',opt,' to ',\
              self.opts.__dict__[opt])
            print('Setting label to ',self.label)

  def clone_all_params_to_models(self):
    for key, val in self.__dict__.items():
      for mm in self.models:
        self.models[mm].__dict__[key] = val

  def create_model(self, model_id):
    self.model_ids.append(model_id)
    self.models[model_id] = ModelParams(model_id)

  def set_default_params(self):
    ''' Setting default parameters here '''
    print('------------------')
    print('Setting default parameters with file ', self.input_file_name)
    #if 'ssephem' not in self.__dict__:
    #  self.__dict__['ssephem'] = 'DE436'
    #  print('Setting default Solar System Ephemeris: DE436')
    #if 'clock' not in self.__dict__:
    #  self.__dict__['clock'] = None
    #  print('Setting a default Enterprise clock convention (check the code)')
    if 'setupsamp' not in self.__dict__:
      self.__dict__['setupsamp'] = False
    if 'psrlist' in self.__dict__:
      self.psrlist = np.loadtxt(self.psrlist, dtype=np.unicode_)
      print('Only using pulsars from psrlist')
    else:
      self.__dict__['psrlist'] = []
      print('Using all available pulsars from .par/.tim directory')
    if 'psrcachedir' not in self.__dict__:
      self.psrcachedir = None
    if 'psrcachefile' not in self.__dict__:
      self.psrcachefile = None
    if 'tm' not in self.__dict__:
      self.tm = 'default'
      print('Setting a default linear timing model')
    if 'inc_events' not in self.__dict__:
      self.inc_events = True
      print('Including transient events to specific pulsar models')
    #if 'sn_sincomp' not in self.__dict__:
    #  self.sn_sincomp = 2
    #  print('Setting number of Fourier sin-cos components to 2')
    #if 'sn_fourier_comp' not in self.__dict__:
    #  self.sn_fourier_comp = 30
    #  print('Setting number of Fourier components to 30')
    if 'fref' not in self.__dict__:
      self.fref = 1400 # MHz
      print('Setting reference radio frequency to 1400 MHz')
    # Copying default priors from StandardModels/CustomModels object
    # Priors are chosen not to be model-specific because HyperModel
    # (which is the only reason to have multiple models) does not support
    # different priors for different models
    for prior_key, prior_default in self.noise_model_obj().priors.items():
      if prior_key not in self.__dict__.keys():
        self.__dict__[prior_key] = prior_default

    # Model-dependent parameters
    for mkey in self.models:

      if 'rs_model' not in self.models[mkey].__dict__:
        self.models[mkey].rs_model = None
        print('Not adding red noise with selections for model',mkey)
      else:
        self.models[mkey].rs_model = read_json_dict(self.models[mkey].rs_model)
      if 'custom_commonpsr' not in self.models[mkey].__dict__:
        self.models[mkey].custom_commonpsr = ''
      if 'custom_singlepsr' not in self.models[mkey].__dict__:
        self.models[mkey].custom_singlepsr = ''
      self.models[mkey].modeldict = dict()

    print('------------------')

  def read_modeldicts(self):
    # Reading general noise model (which will overwrite model-specific ones,
    # if it exists).
    if 'noise_model_file' in self.__dict__.keys():
      self.__dict__['noisemodel'] = read_json_dict(self.noise_model_file)
      self.__dict__['common_signals'] = self.noisemodel['common_signals']
      self.__dict__['model_name'] = self.noisemodel['model_name']
      self.__dict__['universal'] = self.noisemodel['universal']
      del self.noisemodel['common_signals']
      del self.noisemodel['universal']
      del self.noisemodel['model_name']
    # Reading model-specific noise model
    for mkey in self.models:
      if 'noise_model_file' in self.models[mkey].__dict__.keys():
        self.models[mkey].__dict__['noisemodel'] = read_json_dict(\
                                  self.models[mkey].noise_model_file)
        self.models[mkey].__dict__['common_signals'] = \
                                  self.models[mkey].noisemodel['common_signals']
        self.models[mkey].__dict__['model_name'] = \
                                  self.models[mkey].noisemodel['model_name']
        self.models[mkey].__dict__['universal'] = \
                                  self.models[mkey].noisemodel['universal']
        del self.models[mkey].noisemodel['common_signals']
        del self.models[mkey].noisemodel['model_name']
        del self.models[mkey].noisemodel['universal']


  def init_pulsars(self):
      """
      Initiate Enterprise pulsar objects
      """
      directory = self.out
      
      cachedir = directory+'.psrs_cache/'
      if not os.path.exists(cachedir):
          os.makedirs(cachedir)
      
      if not self.psrcachefile==None or (not self.psrcachedir==None and not self.psrlist==[]):
          print('Attempting to load pulsar objects from cache')
          if not self.psrcachedir==None:
              psr_str = ''.join(sorted(self.psrlist)) + self.ssephem
              psr_hash = hashlib.sha1(psr_str.encode()).hexdigest()
              cached_file = self.psrcachedir + psr_hash
          if not self.psrcachefile==None:
              cached_file = self.psrcachefile
          if os.path.exists(cached_file):
              with open(cached_file, 'rb') as fin:
                  print('Loading pulsars from cache')
                  psrs_cache = pickle.load(fin)
          else:
              print('Could not load pulsars from cache: file does not exist')
              psrs_cache = None
      else:
          psrs_cache = None
          print('Condition for loading pulsars from cache is not satisfied')
      
      parfiles = sorted(glob.glob(self.datadir + '/*.par'))
      timfiles = sorted(glob.glob(self.datadir + '/*.tim'))
      print('Number of .par files: ',len(parfiles))
      print('Number of .tim files: ',len(timfiles))
      if len(parfiles)!=len(timfiles):
        print('Error - there should be the same number of .par and .tim files.')
        exit()
      
      if self.allpulsars=='True':
        self.output_dir = self.out
        if psrs_cache == None:
          print('Loading pulsars')
          self.psrlist_new = list()
          for p, t in zip(parfiles, timfiles):
            pname = p.split('/')[-1].split('_')[0].split('.')[0]
            if (pname in self.psrlist) or self.psrlist==[]:
                psr = Pulsar(p, t, ephem=self.ssephem, clk=self.clock,drop_t2pulsar=False)
                self.psrs.append(psr)
                self.psrlist_new.append(pname)
          print('Writing pulsars to cache.\n')
          psr_str = ''.join(sorted(self.psrlist_new)) + self.ssephem
          psr_hash = hashlib.sha1(psr_str.encode()).hexdigest()
          cached_file = cachedir + psr_hash
          with open(cached_file, 'wb') as fout:
            pickle.dump(self.psrs, fout)
        else:
          print('Using pulsars from cache')
          self.psrs = psrs_cache
        # find the maximum time span to set GW frequency sampling
        tmin = [p.toas.min() for p in self.psrs]
        tmax = [p.toas.max() for p in self.psrs]
        self.Tspan = np.max(tmax) - np.min(tmin)
        #psr = []
        exit_message = "PTA analysis has already been carried out using a given parameter file"
      
      elif self.allpulsars=='False':
        self.psrs = Pulsar(parfiles[self.opts.num], timfiles[self.opts.num],drop_t2pulsar=False)#, ephem=params.ssephem, clk=params.clock)
        self.Tspan = self.psrs.toas.max() - self.psrs.toas.min() # observation time in seconds
        self.output_dir = self.out + str(self.opts.num)+'_'+self.psrs.name+'/'

        parfiles = parfiles[self.opts.num]
        timfiles = timfiles[self.opts.num]
        print('Current .par file: ',parfiles)
        print('Current .tim file: ',timfiles)
        directory += str(self.opts.num)+'_'+self.psrs.name+'/'
        exit_message = "This pulsar has already been processed"
        self.psrs = [self.psrs]
      self.directory = directory
      for mkey in self.models:
        self.models[mkey].directory = directory
        if not os.path.exists(directory):
          os.makedirs(directory)

def init_pta(params_all):
  """
  Initiate model and PTA for Enterprise.
  PTA for our code is just one pulsar, since we only
  do parameter estimation for pulsar noise
  """

  ptas = dict.fromkeys(params_all.models)
  for ii, params in params_all.models.items():

    allpsr_model = params_all.noise_model_obj(psr=None,params=params)

    models = list()
    from_par_file = list()
    ecorrexists = np.zeros(len(params_all.psrs))
    
    # Including parameters common for all pulsars
    if params.tm=='default':
      tm = gp_signals.TimingModel()
    elif params.tm=='ridge_regression':
      log10_variance = parameter.Uniform(-20, -10)
      basis = scaled_tm_basis()
      prior = ridge_prior(log10_variance=log10_variance)
      tm = gp_signals.BasisGP(prior, basis, name='ridge')
    if params.tm!='none': m = tm

    # Adding common noise terms for all pulsars
    # Only those common signals are added that are listed in the noise model
    # file, getting Enterprise models from the noise model object.
    for psp, option in params.common_signals.items():
        m += getattr(allpsr_model, psp)(option=option)
  
    # Including single pulsar noise models
    for pnum, psr in enumerate(params_all.psrs):
   
      singlepsr_model = params_all.noise_model_obj(psr=psr, params=params)
 
      # Determine if ecorr is mentioned in par file
      try:
        for key,val in psr.t2pulsar.noisemodel.items():
          if key.startswith('ecorr') or key.startswith('ECORR'):
            ecorrexists[pnum]=True
      except Exception as pint_problem:
        print(pint_problem)
        ecorrexists[pnum]=False

      # Add noise models
      if psr.name in params.noisemodel.keys():
        noise_model_dict_psr = params.noisemodel[psr.name]
      else:
        noise_model_dict_psr = params.universal
      for psp, option in noise_model_dict_psr.items():
        if 'm_sep' in locals():
          m_sep += getattr(singlepsr_model, psp)(option=option)
        else:
          m_sep = getattr(singlepsr_model, psp)(option=option)
  
      models.append(m_sep(psr))
      del m_sep

    pta = signal_base.PTA(models)

    if 'noisefiles' in params.__dict__.keys():
      noisedict = get_noise_dict(psrlist=[p.name for p in params_all.psrs],\
                                 noisefiles=params.noisefiles)
      print('For constant parameters using noise files in PAL2 format')
      pta.set_default_params(noisedict)

    print('Model',ii,'params order: ', pta.param_names)
    np.savetxt(params.directory+'/pars.txt', pta.param_names, fmt='%s')
    ptas[ii]=pta

  return ptas

def checkifconstpar(params):
    [doconstpar_ef, doconstpar_eq, doconstpar_ec] = [False, False, False]
    if np.isscalar(params.efacpr) and params.efacpr<0:
      doconstpar_ef = True
    if np.isscalar(params.equadpr) and params.equadpr<0:
      doconstpar_eq = True
    if np.isscalar(params.ecorrpr) and params.ecorrpr<0:
      doconstpar_ec = True
    return (doconstpar_ef or doconstpar_eq or doconstpar_ec)

def readconstpar(prior,noisemodel,mark,psrname,constpar):
    islg=''
    if not mark=='efac': islg='log10_'
    if np.isscalar(prior) and prior<0:
        for key,val in noisemodel.items(): 
            if key.startswith(mark):
              constpar_dictkey = psrname+'_'+val.flagval+'_'+islg+mark
              if mark=='efac': constpar[constpar_dictkey] = val.val
              else: constpar[constpar_dictkey] = np.log(val.val)
              #constpar[constpar_dictkey] = val.val
    return constpar

def paramstringcut(mystring):
    # Cut string between " " symbols
    start = mystring.find( '"' )
    end = mystring.find( '":' )
    result = mystring[start+1:end]
    return result

def get_noise_from_pal2(noisefile):
    """ THIS SHOULD BE REPLACED LATER ON, IT IS FOR TESTING ONLY
    https://enterprise.readthedocs.io/en/latest/nano9.html"""
    psrname = noisefile.split('/')[-1].split('_noise.txt')[0]
    fin = open(noisefile, 'r')
    lines = fin.readlines()
    params = {}
    for line in lines:
        ln = line.split()
        if 'efac' in line:
            par = 'efac'
            flag = ln[0].split('efac-')[-1]
        elif 'equad' in line:
            par = 'log10_equad'
            flag = ln[0].split('equad-')[-1]
        elif 'jitter_q' in line:
            par = 'log10_ecorr'
            flag = ln[0].split('jitter_q-')[-1]
        elif 'RN-Amplitude' in line:
            par = 'log10_A'
            flag = ''
        elif 'RN-spectral-index' in line:
            par = 'gamma'
            flag = ''
        else:
            break
        if flag:
            name = [psrname, flag, par]
        else:
            name = [psrname, par]
        pname = '_'.join(name)
        params.update({pname: float(ln[1])})
    return params

def get_noise_dict(psrlist,noisefiles):
    """
    Reads in list of pulsar names and returns dictionary
    of {parameter_name: value} for all noise parameters.
    By default the input list is None and we use the 34 pulsars used in
    the stochastic background analysis.
    """

    params = {}
    json_files = sorted(glob.glob(noisefiles + '*.json'))
    for ff in json_files:
        if any([pp in ff for pp in psrlist]):
            with open(ff, 'r') as fin:
                params.update(json.load(fin))
    return params

def get_noise_dict_psr(psrname,noisefiles):
    ''' get_noise_dict for only one pulsar '''
    params = dict()
    with open(noisefiles+psrname+'_noise.json', 'r') as fin:
        params.update(json.load(fin))
    return params

def read_json_dict(json_file):
    out_dict = dict()
    with open(json_file, 'r') as fin:
        out_dict.update(json.load(fin))
    return out_dict

def load_to_dict(filename):
    ''' Load file to dictionary '''
    dictionary = dict()
    with open(filename) as ff:
        for line in ff:
            (key, val) = line.split()
            dictionary[key] = val
    return dictionary

