---
name: bigdata-platform
description: 课程示例：大数据基础平台（Hadoop、HDFS、MapReduce、YARN、Hive、HBase、Spark 等）。学习本课程、做实验或期末复习时使用。也可作为编写其它普通课程学科包的模板。
whenToUse: 课程属于大数据基础平台、大数据技术原理与应用等，或问题涉及 Hadoop 生态组件。
metadata:
  kind: teach
  subject: bigdata-platform
---

# 大数据基础平台

## 讲解方式

- 每个组件讲清四件事：解决什么问题、架构（主从节点各干什么）、读写或计算流程、和其它组件的关系。
- 用对比表区分：HDFS vs 传统文件系统、MapReduce vs Spark、Hive vs HBase、行存 vs 列存。
- 流程题画步骤：HDFS 读写流程、MapReduce 的 Map → Shuffle → Reduce、YARN 提交作业流程。

## 实验辅助

- 常见实验：伪分布式搭建、HDFS Shell 与 Java API、WordCount、Hive 建表查询、Spark RDD 操作。
- 报错先看日志：`logs/` 目录或 YARN Web UI；常见问题是 JAVA_HOME、端口占用、namenode 未格式化、权限。
- 可以用 homework-helper 技能生成实验报告框架。

## 期末复习重点（以老师课件为准）

- Hadoop 生态各组件的作用
- HDFS 副本机制、块大小、NameNode/DataNode/SecondaryNameNode 职责
- MapReduce 编程模型与 Shuffle 过程
- HBase 数据模型（行键、列族、时间戳）
- Spark RDD 宽依赖与窄依赖、DAG、Stage 划分
