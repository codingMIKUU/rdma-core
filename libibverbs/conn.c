#include <stdio.h>
#include "conn.h"
#include <string.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <stdint.h>

int conn_server(char *addr, int port, struct ibv_qp_info *local_qp_info,
		struct ibv_qp_info *remote_qp_info)
{
	int sockfd;
	struct sockaddr_in server_addr;

	// 创建套接字
	if ((sockfd = socket(AF_INET, SOCK_STREAM, 0)) < 0) {
		perror("Socket creation failed");
		return 1;
	}

	// 设置服务器地址
	server_addr.sin_family = AF_INET;
	server_addr.sin_port = htons(port);
	server_addr.sin_addr.s_addr = inet_addr(addr);

	// 连接到服务器
	if (connect(sockfd, (struct sockaddr *)&server_addr,
		    sizeof(server_addr)) < 0) {
		perror("Connection failed");
		close(sockfd);
		return 1;
	}

	// 发送本端QP信息
	if (send(sockfd, local_qp_info, sizeof(*local_qp_info), 0) < 0) {
		perror("Send failed");
		close(sockfd);
		return 1;
	}

	// 接收对端QP信息
	if (recv(sockfd, remote_qp_info, sizeof(*remote_qp_info), 0) < 0) {
		perror("Receive failed");
		close(sockfd);
		return 1;
	}

	// 打印对端QP信息
	//printf("Received remote QP info:\n");
	//print_qp_info(&remote_qp_info);

	// 关闭连接
	close(sockfd);
	return 0;
}
