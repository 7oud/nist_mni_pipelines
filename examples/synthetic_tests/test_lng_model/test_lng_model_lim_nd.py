import os
import ray


from ipl.model_ldd.regress_ldd  import regress_ldd_csv

if __name__ == '__main__':
    # setup data for parallel processing
    ray.init()
    
    regress_ldd_csv('subjects_lim.lst',
    work_prefix='tmp_regress_lim_nr_nd',
    options={
             'protocol': [
                          {'iter':8, 'level':4 },
                          #{'iter':4, 'level':4 },
                          #{'iter':4, 'level':2 },
                         ],
             'parameters': {'smooth_update':2,
                            'smooth_field':2,
                            'conf': { 32:40, 16:40, 8:40, 4:40, 2:40 },
                            'hist_match':True,
                            'max_step':  4.0 },
             'start_level':16,
             'refine': False,
             'blur_int_model': None,
             'blur_vel_model': 4,
             'cleanup': False,
             'debug': True,
             'debias': True,
             'qc': True,
            },
    #regress_model=['data/object_0_4.mnc'],
    model='data/object_0_4.mnc',
    mask='data/mask_0_4.mnc',
    int_par_count=1,
  )
