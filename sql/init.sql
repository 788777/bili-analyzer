-- ============================================================
-- bili-analyzer 数据库初始化脚本
-- 字符集 utf8mb4 / 排序规则 utf8mb4_unicode_ci
-- 所有主键 id 使用 INT UNSIGNED AUTO_INCREMENT，时间字段使用 TIMESTAMP
-- ============================================================

CREATE DATABASE IF NOT EXISTS `bili_analyzer`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `bili_analyzer`;

-- ------------------------------------------------------------
-- 1. 视频基础信息表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `videos` (
    `id`             INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `bvid`           VARCHAR(20)  NOT NULL                COMMENT '视频 BV 号',
    `title`          VARCHAR(255) DEFAULT NULL            COMMENT '视频标题',
    `up_name`        VARCHAR(128) DEFAULT NULL            COMMENT 'UP 主昵称',
    `view_count`     INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '播放量',
    `danmaku_count`  INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '弹幕数',
    `reply_count`    INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '评论数',
    `like_count`     INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '点赞数',
    `coin_count`     INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '投币数',
    `favorite_count` INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '收藏数',
    `publish_time`   TIMESTAMP    NULL DEFAULT NULL       COMMENT '视频发布时间',
    `duration`       INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '视频时长（秒）',
    `description`    TEXT                                 COMMENT '视频简介',
    `scraped_at`     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '采集时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_bvid` (`bvid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='视频基础信息';

-- ------------------------------------------------------------
-- 2. 评论表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `comments` (
    `id`              INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `bvid`            VARCHAR(20)  NOT NULL                COMMENT '所属视频 BV 号',
    `user_name`       VARCHAR(128) DEFAULT NULL            COMMENT '评论用户昵称',
    `user_level`      TINYINT UNSIGNED DEFAULT NULL        COMMENT '用户等级 0-6',
    `user_sex`        VARCHAR(10)  DEFAULT NULL            COMMENT '性别：男/女/保密',
    `user_sign`       VARCHAR(255) DEFAULT NULL            COMMENT '用户个性签名',
    `content`         TEXT         NOT NULL                COMMENT '评论内容',
    `like_count`      INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '评论点赞数',
    `reply_time`      TIMESTAMP    NULL DEFAULT NULL       COMMENT '评论发布时间',
    `ip_location`     VARCHAR(64)  DEFAULT NULL            COMMENT 'IP 属地',
    `sentiment_score` DECIMAL(4,3) DEFAULT NULL            COMMENT '情感得分 0.000-1.000',
    `sentiment_label` VARCHAR(20)  DEFAULT NULL            COMMENT '情感标签：positive/neutral/negative',
    `created_at`      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (`id`),
    KEY `idx_bvid` (`bvid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='视频评论';

-- ------------------------------------------------------------
-- 3. 弹幕表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `danmaku` (
    `id`            INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `bvid`          VARCHAR(20)  NOT NULL                COMMENT '所属视频 BV 号',
    `user_name`     VARCHAR(128) DEFAULT NULL            COMMENT '发送用户昵称（弹幕池通常不返回）',
    `content`       VARCHAR(500) NOT NULL                COMMENT '弹幕内容',
    `send_time`     TIMESTAMP    NULL DEFAULT NULL       COMMENT '弹幕发送时间',
    `danmaku_type`  TINYINT UNSIGNED DEFAULT NULL        COMMENT '弹幕类型/模式：1-3 滚动，4 底部，5 顶部，6 逆向，7 高级，8 代码，9 BAS',
    `created_at`    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (`id`),
    KEY `idx_bvid` (`bvid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='视频弹幕';

-- ------------------------------------------------------------
-- 4. 用户画像汇总表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `user_profile_summary` (
    `id`                INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `bvid`              VARCHAR(20)  NOT NULL                COMMENT '所属视频 BV 号',
    `cluster_id`        INT UNSIGNED NOT NULL                COMMENT '聚类簇编号',
    `cluster_label`     VARCHAR(50)  DEFAULT NULL            COMMENT '聚类簇标签（如：活跃互动型）',
    `user_count`        INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '该簇用户数',
    `avg_level`         DECIMAL(3,2) DEFAULT NULL            COMMENT '该簇平均用户等级 0.00-6.00',
    `top_keywords`      VARCHAR(500) DEFAULT NULL            COMMENT '该簇高频关键词，逗号分隔',
    `top_ip_locations`  VARCHAR(500) DEFAULT NULL            COMMENT '该簇高频 IP 属地，逗号分隔',
    `created_at`        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '生成时间',
    PRIMARY KEY (`id`),
    KEY `idx_bvid` (`bvid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户画像聚类汇总';
