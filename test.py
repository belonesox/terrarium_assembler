import os
import pip

# dd = pip.main(['show', '-f', 'reedsolo'])
# print(dd)

import pkg_resources
from pip._vendor import pkg_resources

for dist in pkg_resources.working_set:
    if dist.key in ['reedsolo']:
        if dist.has_metadata('RECORD'):
            lines = dist.get_metadata_lines('RECORD')
            paths = [line.split(',')[0] for line in lines]
            paths = [os.path.join(dist.location, p) for p in paths]
            # file_list = [os.path.relpath(p, dist.location) for p in paths]
            print(paths)   


