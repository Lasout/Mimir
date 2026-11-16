"""Suite de tests tradeHelper.

OMP_NUM_THREADS=1 : les tests font tourner sklearn (OpenMP) dans le processus
principal, puis un test utilise un pool multiprocessing en fork. Un fork dans un
processus multi-threadé (threadpool OpenMP) peut deadlocker : on fige OpenMP à 1
thread pour toute la suite (les perfs de calcul ne sont pas testées ici).
"""

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
