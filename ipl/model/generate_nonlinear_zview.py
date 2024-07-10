import os
import sys
import csv
import traceback
import json

# MINC stuff
from ipl.minc_tools import mincTools, mincError

from ipl.model.structures import MriDataset, MriTransform, MRIEncoder
from ipl.model.filter import generate_flip_sample, normalize_sample
from ipl.model.filter import average_samples, average_stats
from ipl.model.filter import calculate_diff_bias_field, average_bias_fields
from ipl.model.filter import resample_and_correct_bias

from ipl.model.registration import linear_register_step
from ipl.model.registration import non_linear_register_step
from ipl.model.registration import dd_register_step
from ipl.model.registration import ants_register_step
from ipl.model.registration import elastix_register_step
from ipl.model.registration import average_transforms
from ipl.model.resample import concat_resample
from ipl.model.resample import concat_resample_nl

import ray


def generate_nonlinear_average(
    samples,
    initial_model=None,
    output_model=None,
    output_model_sd=None,
    prefix='.',
    options={},
    skip=0,
    stop_early=100000,
):
    """perform iterative model creation"""

    # use first sample as initial model
    if not initial_model:
        initial_model = samples[0]

    # current estimate of template
    current_model = initial_model
    current_model_sd = None

    sd = []
    corr_samples = []
    corr_transforms = []

    protocol = options.get('protocol', [{'iter': 4, 'level': 32}, {'iter': 4, 'level': 32}])

    cleanup = options.get('cleanup', False)
    symmetric = options.get('symmetric', False)
    parameters = options.get('parameters', None)
    downsample_ = options.get('downsample', None)
    start_level = options.get('start_level', None)
    use_median = options.get('median', False)

    models = []
    models_sd = []

    if symmetric:
        flipdir = prefix + os.sep + 'flip'
        if not os.path.exists(flipdir):
            os.makedirs(flipdir)

        flip_all = []
        # generate flipped versions of all scans
        for i, s in enumerate(samples):
            _s_name = os.path.basename(s.scan).rsplit('.gz', 1)[0]
            s.scan_f = prefix + os.sep + 'flip' + os.sep + _s_name

            if s.mask is not None:
                s.mask_f = prefix + os.sep + 'flip' + os.sep + 'mask_' + _s_name

            flip_all.append(generate_flip_sample.remote(s))

        ray.get(flip_all)

    # go through all the iterations
    it = 0
    for i, p in enumerate(protocol):
        downsample = p.get('downsample', downsample_)

        for _ in range(1, p['iter'] + 1):
            it += 1

            # this will be a model for next iteration actually

            # 1 register all subjects to current template
            next_model = MriDataset(prefix=prefix, iter=it, name='avg', has_mask=current_model.has_mask())
            next_model_sd = MriDataset(prefix=prefix, iter=it, name='sd', has_mask=current_model.has_mask())

            it_prefix = prefix + os.sep + str(it)
            if not os.path.exists(it_prefix):
                os.makedirs(it_prefix)

            inv_transforms = []
            fwd_transforms = []

            start = start_level if it == 1 else None

            for i, s in enumerate(samples):
                sample_xfm = MriTransform(name=s.name, prefix=it_prefix, iter=it)
                sample_inv_xfm = MriTransform(name=s.name + '_inv', prefix=it_prefix, iter=it)

                prev_transform = None

                if it > 1:
                    prev_transform = corr_transforms[i]

                non_linear_register_step(
                    s,              # [in]
                    current_model,  # [in]
                    sample_xfm,     # [out]
                    output_invert=sample_inv_xfm,   # [out] 
                    init_xfm=prev_transform,        # [in]
                    symmetric=symmetric,
                    parameters=parameters,
                    level=p['level'],
                    start=start,
                    work_dir=prefix,
                    downsample=downsample,
                )

                inv_transforms.append(sample_inv_xfm)
                fwd_transforms.append(sample_xfm)

            if it > 1:
                # remove information from previous iteration
                [s.cleanup() for s in corr_samples]
                [x.cleanup() for x in corr_transforms]

            # 2 average all transformations
            avg_inv_transform = MriTransform(name='avg_inv', prefix=it_prefix, iter=it)
            average_transforms(inv_transforms, avg_inv_transform, nl=True, symmetric=symmetric)

            # 3 concatenate correction and resample
            corr_samples = []
            corr_transforms = []
            for i, s in enumerate(samples):
                c = MriDataset(prefix=it_prefix, iter=it, name=s.name)
                x = MriTransform(name=s.name + '_corr', prefix=it_prefix, iter=it)

                concat_resample_nl(
                    s,
                    fwd_transforms[i],
                    avg_inv_transform,
                    c,
                    x,
                    current_model,
                    level=p['level'],
                    symmetric=symmetric,
                    qc=False,
                )
                corr_transforms.append(x)
                corr_samples.append(c)

            # cleanup transforms
            [x.cleanup() for x in inv_transforms]
            [x.cleanup() for x in fwd_transforms]
            avg_inv_transform.cleanup()

            # 4 average resampled samples to create new estimate
            average_samples(
                corr_samples,   # [in]
                next_model,     # [out]
                next_model_sd,  # [out]
                symmetric=symmetric,
                symmetrize=symmetric,
                median=use_median,
            )

            # remove previous template estimate
            if it > 1:
                models.append(next_model)
                models_sd.append(next_model_sd)

            current_model = next_model
            current_model_sd = next_model_sd

            result = average_stats(next_model, next_model_sd)
            sd.append(result)

    # copy output to the destination
    with open(prefix + os.sep + 'stats.txt', 'w') as f:
        for s in sd:
            f.write("{}\n".format(ray.get(s)))

    results = {
        'model': current_model,
        'model_sd': current_model_sd,
        'xfm': corr_transforms,
        'biascorr': None,
        'scan': corr_samples,
        'symmetric': symmetric,
        'samples': samples,
    }

    # keep the final model
    models.pop()
    models_sd.pop()

    # delete unneeded models
    [m.cleanup() for m in models]
    [m.cleanup() for m in models_sd]

    return results


def generate_nonlinear_model(samples, model=None, mask=None, work_prefix=None, options={}, skip=0, stop_early=100000):
    internal_sample = []
    try:
        for i in samples:
            s = MriDataset(scan=i[0], mask=i[1])
            internal_sample.append(s)

        internal_model = None
        if model is not None:
            internal_model = MriDataset(scan=model, mask=mask)

        if work_prefix is not None and not os.path.exists(work_prefix):
            os.makedirs(work_prefix)

        return generate_nonlinear_average(
            internal_sample, internal_model, prefix=work_prefix, options=options, skip=skip, stop_early=stop_early
        )

    except mincError as e:
        print("Exception in generate_nonlinear_model:{}".format(str(e)))
        traceback.print_exc(file=sys.stdout)
        raise
    except:
        print("Exception in generate_nonlinear_model:{}".format(sys.exc_info()[0]))
        traceback.print_exc(file=sys.stdout)
        raise


# kate: space-indent on; indent-width 4; indent-mode python;replace-tabs on;word-wrap-column 80;show-tabs on
