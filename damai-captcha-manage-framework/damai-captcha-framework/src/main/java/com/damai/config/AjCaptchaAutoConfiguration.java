package com.damai.config;

import com.damai.properties.AjCaptchaProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: AjCaptchaAutoConfiguration
 * @author: 阿星不是程序员
 **/

@Configuration //标记这是一个配置类，Spring会自动处理这个类中的Bean定义，会被组件扫描器识别
@EnableConfigurationProperties(AjCaptchaProperties.class) //启用对AjCaptchaProperties配置属性的支持，将application.yml/properties中的配置绑定到AjCaptchaProperties类 使AjCaptchaProperties成为Spring容器中的Bean
@ComponentScan("com.damai") //扫描指定包路径下的组件自动注册@Component、@Service、@Repository、@Controller等注解的类
@Import({AjCaptchaServiceAutoConfiguration.class, AjCaptchaStorageAutoConfiguration.class}) //导入其他配置类，可以引入第三方库的配置，实现模块化的配置管理
public class AjCaptchaAutoConfiguration {
}
