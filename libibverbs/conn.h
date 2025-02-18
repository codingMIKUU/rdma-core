#ifndef INFINIBAND_CONN_H
#define INFINIBAND_CONN_H
#include "verbs.h"


int conn_server(char *addr, int port,struct ibv_qp_info *local_qp_info,struct ibv_qp_info *remote_qp_info);


#endif /* INFINIBAND_CONN_H */