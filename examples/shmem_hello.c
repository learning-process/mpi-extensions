#include <shmem.h>

#include <stdio.h>

int main(void) {
  shmem_init();

  const int pe = shmem_my_pe();
  const int npes = shmem_n_pes();
  printf("SHMEM PE %d of %d\n", pe, npes);

  shmem_finalize();
  return 0;
}
